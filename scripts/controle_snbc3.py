"""
scripts/controle_snbc3.py
=========================
Contrôle des données SNBC3 transmises par la DGEC, avant tout retraitement.

Les données SNBC3 ont été livrées par versions successives (run 3, puis run 3
bis pour les GES DROM). Une anomalie signalée sur un envoi doit être re-testée sur le suivant, sinon rien
ne garantit qu'elle a été corrigée - ni qu'une correction n'en a pas introduit
une autre. Ce script exécute les contrôles déclarés dans
`config/anomalies_snbc3.yaml` et indique, pour chacun, s'il est encore PRESENT
ou CORRIGE sur le fichier en place.

Il cherche en outre les **ruptures non déclarées** : tout pas dont le rythme
s'écarte fortement de celui de sa propre série, hors postes déjà inscrits au
registre. Une nouvelle livraison qui casserait une série encore saine serait
ainsi détectée sans intervention.

Trois surfaces de contrôle, des données d'entrée jusqu'au livrable :

  1. **onglets bruts DGEC** : ruptures, divergences entre sources, cohérence
     entre le bloc de synthèse et les blocs de détail d'un même onglet ;
  2. **contrôles internes du classeur** : les lignes « Vérification / Delta
     total » que le retraitement calcule lui-même à chaque étage, et qui
     doivent être nulles ;
  3. **trajectoires retraitées** : ruptures sur les séries effectivement
     livrées aux territoires, par code traj supra.

Ce script n'est PAS un test : aucune assertion, code de sortie toujours 0. Il
produit la matière d'un retour à la DGEC.

L'export CSV couvre **les quatre sections**, distinguées par la colonne
`section` : contrôles du registre, ruptures non déclarées, vérifications
internes du classeur, ruptures sur les trajectoires retraitées. Tout ce qui est
affiché est exporté - un constat visible à l'écran mais absent du fichier
serait perdu au moment de le transmettre.

DEUX COLONNES DE STATUT, À NE PAS CONFONDRE
  `statut_registre` : ce que **le registre déclare**, donc ce qu'on sait de
      l'anomalie hors de toute exécution - `ouverte`, `corrigee`, ou `n/a` /
      `non déclarée` pour ce qui ne vient pas du registre.
  `etat`            : ce que **la mesure constate** sur le fichier en place -
      `PRESENTE`, `CORRIGEE`, `N.D.` (contrôle non évaluable, poste ou année
      absents), `SIGNALE` pour les ruptures détectées, qui n'ont pas de
      référence à laquelle se comparer.

C'est le **croisement des deux** qui porte l'information : `corrigee` +
`PRESENTE` est une régression, une anomalie qu'on croyait réglée et qui
réapparaît ; `ouverte` + `CORRIGEE` signale au contraire qu'elle a été traitée
et que l'entrée peut passer en `corrigee` dans le registre.

Usage :
    uv run python scripts/controle_snbc3.py
    uv run python scripts/controle_snbc3.py --seuil-annuel 0.4
"""

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Postes dont une variation forte est normale (fin de trajectoire proche de
# zéro, où le ratio d'une année à l'autre n'a plus de sens).
_TOLERANCE_FIN_TRAJ = 0.3   # MtCO2e : en deçà, on n'évalue pas les ratios


_CACHE: dict[tuple[str, str], tuple[dict, list[int]]] = {}


def _lire_onglet(path: Path, onglet: str) -> tuple[dict, list[int]]:
    """
    Lit un onglet brut DGEC et retourne ({poste: {annee: valeur}}, annees).

    La ligne d'en-tête et la colonne des libellés sont détectées, les trois
    onglets concernés n'ayant pas la même mise en page. Résultat mis en cache :
    chaque onglet est relu par plusieurs contrôles.
    """
    import openpyxl

    cle = (str(path), onglet)
    if cle in _CACHE:
        return _CACHE[cle]

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    if onglet not in wb.sheetnames:
        wb.close()
        raise SystemExit(f"Onglet '{onglet}' absent de {path.name} : {wb.sheetnames}")
    rows = list(wb[onglet].iter_rows(values_only=True))
    wb.close()

    # En-tête = première ligne portant au moins 5 années
    hdr_i, cols = None, {}
    for i, r in enumerate(rows):
        ans = {j: int(c) for j, c in enumerate(r)
               if isinstance(c, (int, float)) and 2000 < c < 2100
               and float(c).is_integer()}
        if len(ans) >= 5:
            hdr_i, cols = i, ans
            break
    if hdr_i is None:
        raise SystemExit(f"Aucune ligne d'en-tête d'années trouvée dans '{onglet}'")

    # Colonne des libellés = dernière colonne textuelle avant la 1re année
    premiere_annee = min(cols)
    candidates = range(premiere_annee - 1, -1, -1)
    lab_col = next(
        (j for j in candidates
         if sum(1 for r in rows[hdr_i + 1:hdr_i + 20]
                if len(r) > j and isinstance(r[j], str) and r[j].strip()) >= 3),
        0,
    )

    series = {}
    for r in rows[hdr_i + 1:]:
        if len(r) <= lab_col or not isinstance(r[lab_col], str):
            continue
        lab = r[lab_col].strip()
        if not lab:
            continue
        vals = {a: r[j] for j, a in cols.items()
                if len(r) > j and isinstance(r[j], (int, float))}
        if vals and lab not in series:
            series[lab] = vals
    _CACHE[cle] = (series, sorted(cols.values()))
    return _CACHE[cle]


def _poste(series: dict, nom: str):
    """Série d'un poste, par égalité puis par préfixe."""
    if nom in series:
        return series[nom]
    return next((v for k, v in series.items() if k.startswith(nom)), None)


# ---------------------------------------------------------------------------
# Contrôles
# ---------------------------------------------------------------------------

def _ctrl_rupture(path, anomalie, ctrl):
    s = _poste(_lire_onglet(path, anomalie["onglet"])[0], anomalie["poste"])
    if s is None:
        return None, f"poste '{anomalie['poste']}' introuvable"
    a0, a1 = ctrl["annee_avant"], ctrl["annee_apres"]
    v0, v1 = s.get(a0), s.get(a1)
    if v0 is None or v1 is None:
        return None, f"années {a0}/{a1} absentes de la livraison"
    if abs(v0) < 1e-12:
        return None, f"valeur nulle en {a0}"
    ratio = v1 / v0
    present = abs(ratio - 1) > (ctrl["ratio_max"] - 1)
    return present, f"{a0}={v0:,.3f} -> {a1}={v1:,.3f} (x{ratio:,.2f}, seuil x{ctrl['ratio_max']})"


def _ctrl_divergence(path, anomalie, ctrl):
    s, _ = _lire_onglet(path, anomalie["onglet"])
    ref, _ = _lire_onglet(path, ctrl["onglet_reference"])
    a = _poste(s, anomalie["poste"])
    b = _poste(ref, ctrl["poste_reference"])
    if a is None or b is None:
        return None, "poste introuvable dans l'une des deux sources"
    f = ctrl.get("facteur_reference", 1.0)
    ecarts = []
    for an in ctrl["annees"]:
        va, vb = a.get(an), b.get(an)
        if va is None or vb is None:
            continue
        vb *= f
        if abs(vb) > 1e-12:
            ecarts.append((an, 100 * (va / vb - 1)))
    if not ecarts:
        return None, "aucune année commune"
    pire = max(ecarts, key=lambda t: abs(t[1]))
    present = abs(pire[1]) > ctrl["ecart_max_pct"]
    return present, (f"écart max {pire[1]:+,.0f} % en {pire[0]} "
                     f"(seuil ±{ctrl['ecart_max_pct']} %), sur {len(ecarts)} années")


def _ctrl_coherence_lignes(path, anomalie, ctrl):
    """
    Deux lignes d'un même onglet censées porter la même grandeur.

    Les onglets bruts DGEC juxtaposent un bloc de synthèse (une ligne par
    secteur SECTEN) et des blocs de détail terminés par un total. Les deux
    doivent coïncider ; un écart signale que le détail et la synthèse ne
    décrivent plus la même chose, ce qu'aucun contrôle de rupture ne voit
    puisque chaque série prise isolément peut rester lisse.
    """
    s, _ = _lire_onglet(path, anomalie["onglet"])
    a = _poste(s, anomalie["poste"])
    b = _poste(s, ctrl["poste_reference"])
    if a is None or b is None:
        return None, "l'un des deux postes est introuvable"
    ecarts = []
    for an, va in a.items():
        vb = b.get(an)
        if vb is None or abs(vb) < 1e-12:
            continue
        ecarts.append((an, 100 * (va / vb - 1)))
    if not ecarts:
        return None, "aucune année comparable"
    pire = max(ecarts, key=lambda t: abs(t[1]))
    present = abs(pire[1]) > ctrl["ecart_max_pct"]
    return present, (f"écart max {pire[1]:+,.0f} % en {pire[0]} "
                     f"(seuil ±{ctrl['ecart_max_pct']} %), sur {len(ecarts)} années")


def _ctrl_valeurs_negatives(path, anomalie, ctrl):
    """
    Émissions brutes négatives : physiquement impossible hors puits de carbone
    et hors captage, qui sont exclus par motif.

    Le contrôle balaie tous les postes de l'onglet : une valeur négative isolée
    n'est pas une variation, aucun contrôle de rupture ne la voit.
    """
    s, _ = _lire_onglet(path, anomalie["onglet"])
    depuis = ctrl.get("depuis", 0)
    seuil = ctrl.get("seuil", 0.001)
    motifs = [m.lower() for m in (ctrl.get("exclure_motifs") or [])]

    touches = []
    for nom, vals in s.items():
        if any(m in nom.lower() for m in motifs):
            continue
        neg = {a: v for a, v in vals.items() if a >= depuis and v < -seuil}
        if neg:
            pire = min(neg.items(), key=lambda t: t[1])
            touches.append(f"{nom[:44]} ({pire[0]} : {pire[1]:+.3f})")
    present = bool(touches)
    detail = (f"{len(touches)} poste(s) : " + " ; ".join(touches[:4]) if touches
              else f"aucune valeur < {-seuil} depuis {depuis}")
    return present, detail


def _ctrl_colonnes_identiques(path, anomalie, ctrl):
    """
    Colonnes-années dupliquées : plusieurs années portant le **même vecteur de
    valeurs sur l'ensemble des postes**, signe d'un copier-coller de colonne.

    Le test porte sur la colonne entière, pas sur une série : un poste seul qui
    reste constant en fin de trajectoire est un palier légitime, pas une
    anomalie de saisie.
    """
    s, annees = _lire_onglet(path, anomalie["onglet"])
    depuis, n_min = ctrl.get("depuis", 0), ctrl["annees_min"]
    ans = [a for a in annees if a >= depuis]
    if len(ans) < n_min:
        return None, f"moins de {n_min} années depuis {depuis}"

    vecteurs = {}
    for a in ans:
        vec = tuple(round(v[a], 10) if a in v else None for v in s.values())
        vecteurs.setdefault(vec, []).append(a)
    groupes = [sorted(g) for g in vecteurs.values() if len(g) >= n_min]

    present = bool(groupes)
    detail = (f"colonnes dupliquées : {groupes}" if groupes
              else f"aucune colonne dupliquée parmi {ans} ({len(s)} postes)")
    return present, detail


_CONTROLES = {
    "rupture": _ctrl_rupture,
    "divergence_sources": _ctrl_divergence,
    "coherence_lignes": _ctrl_coherence_lignes,
    "valeurs_negatives": _ctrl_valeurs_negatives,
    "colonnes_identiques": _ctrl_colonnes_identiques,
}


# ---------------------------------------------------------------------------
# Onglets retraités : contrôles internes et trajectoires livrées
# ---------------------------------------------------------------------------

def _verifications_internes(path, onglets) -> list[dict]:
    """
    Lignes « Vérification / Delta total » que le classeur calcule lui-même à
    chaque étage du retraitement. Elles doivent être nulles : c'est le contrôle
    le plus direct de la chaîne SECTEN -> PCAET -> pivots, et il est déjà écrit
    dans le classeur.

    Seules les lignes dont le sous-libellé commence par « Delta » sont des
    écarts : les autres lignes « Vérification » portent des totaux.
    """
    import openpyxl

    out = []
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    for onglet in onglets:
        if onglet not in wb.sheetnames:
            continue
        rows = list(wb[onglet].iter_rows(values_only=True))
        an = [i for i, c in enumerate(rows[0])
              if isinstance(c, (int, float)) and 2000 < c < 2100]
        for i, r in enumerate(rows, 1):
            a = str(r[0]).strip() if r[0] else ""
            b = str(r[1]).strip() if len(r) > 1 and r[1] else ""
            if not a.startswith("Vérification") or not b.startswith("Delta"):
                continue
            vals = [r[j] for j in an if j < len(r) and isinstance(r[j], (int, float))]
            out.append({"onglet": onglet, "ligne": i, "controle": b,
                        "delta_max": max((abs(v) for v in vals), default=0.0)})
    wb.close()
    return out


def _lire_retraite(path, onglet) -> dict:
    """Trajectoires livrées d'un onglet retraité : {code traj supra: {annee: valeur}}."""
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    if onglet not in wb.sheetnames:
        wb.close()
        return {}
    rows = list(wb[onglet].iter_rows(values_only=True))
    wb.close()
    hdr = rows[0]
    cols = {i: int(c) for i, c in enumerate(hdr)
            if isinstance(c, (int, float)) and 2000 < c < 2100}
    CODE = 3                                   # colonne D
    series = {}
    for r in rows[1:]:
        if len(r) <= CODE or not isinstance(r[CODE], str) or not r[CODE].strip():
            continue
        code = r[CODE].strip()
        if code in series:
            continue
        series[code] = {a: r[j] for j, a in cols.items()
                        if j < len(r) and isinstance(r[j], (int, float))}
    return series


def _ruptures_non_declarees(path, onglets, declares, seuil_annuel, facteur_ecart):
    """
    Ruptures de série, hors postes déjà inscrits au registre.

    Un simple seuil sur le ratio d'une année à l'autre ne fonctionne pas : les
    trajectoires SNBC3 comportent des paliers de décarbonation légitimes et
    parfois très raides en fin de période, qu'il signalerait tous. Le critère
    retenu compare chaque pas au **rythme propre de la série** : est signalé un
    pas dont le taux annualisé dépasse `seuil_annuel` **et** vaut plus de
    `facteur_ecart` fois le taux médian des autres pas de la même série. Une
    accélération régulière passe, une discontinuité isolée ressort.

    Les pas changeant de signe sont ignorés (le ratio n'y a pas de sens), de
    même que les valeurs proches de zéro en fin de trajectoire.
    """
    trouvees = []
    for onglet in onglets:
        s, annees = _lire_onglet(path, onglet)
        for nom, vals in s.items():
            # Correspondance par préfixe : le registre porte des libellés courts
            # (« sous-total Usage des bâtiments tertiaires »), la feuille des
            # libellés complets (« ... et activités tertiaires »).
            if any(o == onglet and nom.startswith(p) for o, p in declares):
                continue
            if nom.lower().startswith(("total", "dont")):
                continue
            suite = [(a, vals[a]) for a in annees if a in vals]

            pas = []
            for (a0, v0), (a1, v1) in zip(suite, suite[1:]):
                if abs(v0) < _TOLERANCE_FIN_TRAJ or abs(v1) < _TOLERANCE_FIN_TRAJ:
                    continue
                if v0 * v1 <= 0:            # changement de signe : ratio sans objet
                    continue
                dt = max(a1 - a0, 1)
                taux = abs(v1 / v0) ** (1 / dt) - 1
                pas.append((a0, a1, v0, v1, taux))
            if len(pas) < 3:                # trop court pour dégager un rythme
                continue

            taux_abs = sorted(abs(t) for *_, t in pas)
            median = taux_abs[len(taux_abs) // 2]
            for a0, a1, v0, v1, taux in pas:
                if abs(taux) <= seuil_annuel:
                    continue
                if median > 0 and abs(taux) <= facteur_ecart * median:
                    continue
                trouvees.append({
                    "onglet": onglet, "poste": nom, "annee_avant": a0,
                    "annee_apres": a1, "valeur_avant": v0, "valeur_apres": v1,
                    "taux_annualise_pct": 100 * taux,
                    "rythme_median_pct": 100 * median,
                })
    return trouvees


def main():
    import pandas as pd
    import yaml

    from config.settings import OUTPUT_DIR, ROOT_DIR, SNBC3_TRAVAIL_PATH

    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--fichier", type=Path, default=SNBC3_TRAVAIL_PATH,
                   help="Classeur de travail SNBC3 (défaut : SNBC3_TRAVAIL_PATH ; le classeur publié ne porte pas les onglets bruts DGEC)")
    p.add_argument("--seuil-annuel", type=float, default=0.25,
                   help="Taux annualisé au-delà duquel un pas est candidat (défaut : 0.25)")
    p.add_argument("--tolerance-delta", type=float, default=1e-6,
                   help="Seuil au-delà duquel un contrôle interne du classeur est "
                        "considéré rompu (défaut : 1e-6)")
    p.add_argument("--facteur-ecart", type=float, default=4.0,
                   help="Multiple du rythme médian de la série au-delà duquel le pas "
                        "est signalé (défaut : 4)")
    args = p.parse_args()
    if not args.fichier.exists():
        sys.exit(f"Classeur de travail SNBC3 introuvable : {args.fichier}")

    with open(ROOT_DIR / "config" / "anomalies_snbc3.yaml", encoding="utf-8") as f:
        registre = yaml.safe_load(f)

    print(f"\n{'=' * 94}")
    print(f"CONTRÔLE DES DONNÉES SNBC3 - {args.fichier.name}")
    print(f"Registre : {len(registre['anomalies'])} anomalie(s) déclarée(s), "
          f"millésime attendu « {registre['meta']['millesime_controle']} »")
    print(f"{'=' * 94}")

    lignes = []
    for a in registre["anomalies"]:
        print(f"\n[{a['id']}] {a['titre']}")
        sig = a.get("signale")
        print(f"   statut au registre : {a['statut']}  "
              + (f"(signalé le {sig['date']} à {sig['a']})" if sig else "(non signalé)"))
        for ctrl in a["controles"]:
            fn = _CONTROLES.get(ctrl["type"])
            if fn is None:
                print(f"   ? type de contrôle inconnu : {ctrl['type']}")
                continue
            present, detail = fn(args.fichier, a, ctrl)
            etat = ("N.D." if present is None
                    else ("PRESENTE" if present else "CORRIGEE"))
            marque = {"PRESENTE": "x", "CORRIGEE": "v", "N.D.": "?"}[etat]
            print(f"   [{marque}] {ctrl['type']:<20} {etat:<9} {detail}")
            lignes.append({"section": "registre", "onglet": a["onglet"],
                           "objet": a["id"], "statut_registre": a["statut"],
                           "controle": ctrl["type"], "etat": etat, "detail": detail})

    # Régressions : une anomalie déclarée corrigée qui redevient présente
    regress = [l for l in lignes
               if l["statut_registre"] == "corrigee" and l["etat"] == "PRESENTE"]
    if regress:
        print(f"\n{'!' * 94}")
        print("REGRESSION : anomalie(s) déclarée(s) corrigée(s) et de nouveau détectée(s) :")
        for l in regress:
            print(f"  - {l['objet']} / {l['controle']} : {l['detail']}")
        print(f"{'!' * 94}")

    # Ruptures non déclarées
    declares = {
        (a["onglet"], p)
        for a in registre["anomalies"]
        for p in ([a["poste"]] if a.get("poste") else []) + (a.get("postes_couverts") or [])
    }
    onglets = sorted({a["onglet"] for a in registre["anomalies"]})
    nouvelles = _ruptures_non_declarees(args.fichier, onglets, declares,
                                    args.seuil_annuel, args.facteur_ecart)
    print(f"\n{'-' * 94}")
    print(f"RUPTURES NON DÉCLARÉES (taux annualisé > {args.seuil_annuel:.0%} ET plus de "
          f"{args.facteur_ecart:g}x le rythme médian de la série,")
    print(f"hors postes du registre, valeurs > {_TOLERANCE_FIN_TRAJ} MtCO2e)")
    print(f"{'-' * 94}\n")
    if nouvelles:
        df = pd.DataFrame(nouvelles)
        print(df.to_string(index=False, float_format=lambda v: f"{v:,.3f}"))
        print()
        print("À examiner : soit une évolution réelle du scénario, soit une "
              "anomalie à inscrire au registre.")
        lignes += [{
            "section": "rupture_non_declaree", "onglet": n["onglet"],
            "objet": n["poste"], "statut_registre": "non déclarée",
            "controle": "rupture_detectee", "etat": "SIGNALE",
            "detail": (f"{n['annee_avant']} -> {n['annee_apres']} : "
                       f"{n['valeur_avant']:,.3f} -> {n['valeur_apres']:,.3f} "
                       f"({n['taux_annualise_pct']:+,.1f} %/an contre "
                       f"{n['rythme_median_pct']:,.1f} % median)"),
        } for n in nouvelles]
    else:
        print("Aucune.")

    # ── Onglets retraités : contrôles internes du classeur ──────────────────
    from config.settings import SNBC3_SHEETS
    retraites = [n for n in dict.fromkeys(
        list(SNBC3_SHEETS.values()) + [registre["meta"].get("onglet_kyoto", "SNBC3 retraitée Kyoto")])]
    verifs = _verifications_internes(args.fichier, retraites)
    print()
    print(f"{'-' * 94}")
    print("CONTRÔLES INTERNES DES ONGLETS RETRAITÉS (lignes « Vérification / Delta total »)")
    print(f"{'-' * 94}")
    print()
    if verifs:
        rompus = [v for v in verifs if v["delta_max"] > args.tolerance_delta]
        par_onglet = {}
        for v in verifs:
            par_onglet.setdefault(v["onglet"], []).append(v["delta_max"])
        for onglet, deltas in par_onglet.items():
            etat = "OK" if max(deltas) <= args.tolerance_delta else "ECART"
            print(f"  {onglet:<26} {len(deltas):>3} contrôles, "
                  f"|delta| max = {max(deltas):.2e}   {etat}")
        if rompus:
            print()
            print("  Contrôles rompus :")
            for v in rompus:
                print(f"    {v['onglet']} L{v['ligne']} ({v['controle']}) : {v['delta_max']:.4g}")
            print()
            print("  Le retraitement ne boucle pas : la trajectoire livrée ne "
                  "correspond pas à la somme de ses composantes.")
        lignes += [{"section": "verification_interne", "onglet": v["onglet"],
                    "objet": f"L{v['ligne']} {v['controle']}", "statut_registre": "n/a",
                    "controle": "verification_interne",
                    "etat": "PRESENTE" if v["delta_max"] > args.tolerance_delta else "CORRIGEE",
                    "detail": f"|delta| max = {v['delta_max']:.2e}"} for v in verifs]
    else:
        print("  Aucune ligne de vérification trouvée dans les onglets retraités.")

    # ── Ruptures sur les trajectoires livrées ───────────────────────────────
    print()
    print(f"{'-' * 94}")
    print("RUPTURES SUR LES TRAJECTOIRES RETRAITÉES (par code traj supra)")
    print(f"{'-' * 94}")
    print()
    trouve = []
    for onglet in retraites:
        series = _lire_retraite(args.fichier, onglet)
        for code, vals in series.items():
            suite = sorted(vals.items())
            pas = []
            for (a0, v0), (a1, v1) in zip(suite, suite[1:]):
                if abs(v0) < _TOLERANCE_FIN_TRAJ or abs(v1) < _TOLERANCE_FIN_TRAJ:
                    continue
                if v0 * v1 <= 0:
                    continue
                dt = max(a1 - a0, 1)
                pas.append((a0, a1, v0, v1, abs(v1 / v0) ** (1 / dt) - 1))
            if len(pas) < 3:
                continue
            taux = sorted(abs(t) for *_, t in pas)
            med = taux[len(taux) // 2]
            for a0, a1, v0, v1, t in pas:
                if abs(t) > args.seuil_annuel and (med <= 0 or abs(t) > args.facteur_ecart * med):
                    trouve.append({"onglet": onglet, "code": code, "annee_avant": a0,
                                   "annee_apres": a1, "valeur_avant": v0,
                                   "valeur_apres": v1, "taux_annualise_pct": 100 * t})
    if trouve:
        print(pd.DataFrame(trouve).to_string(index=False, float_format=lambda v: f"{v:,.3f}"))
        print()
        print("Ces ruptures sont celles que verront les territoires : elles héritent "
              "des données d'entrée ci-dessus.")
        lignes += [{
            "section": "rupture_retraitee", "onglet": t["onglet"],
            "objet": t["code"], "statut_registre": "n/a",
            "controle": "rupture_detectee", "etat": "SIGNALE",
            "detail": (f"{t['annee_avant']} -> {t['annee_apres']} : "
                       f"{t['valeur_avant']:,.3f} -> {t['valeur_apres']:,.3f} "
                       f"({t['taux_annualise_pct']:+,.1f} %/an)"),
        } for t in trouve]
    else:
        print("Aucune.")

    if lignes:
        COLS = ["section", "onglet", "objet", "statut_registre", "controle",
                "etat", "detail"]
        df_out = pd.DataFrame(lignes)[COLS]
        out = OUTPUT_DIR / f"controle_snbc3_{date.today()}.csv"
        df_out.to_csv(out, index=False, sep=";", encoding="utf-8-sig")

        print()
        print("=" * 94)
        print("BILAN - toutes sections")
        print("=" * 94)
        print()
        recap = df_out.groupby(["section", "etat"]).size().rename("nb").reset_index()
        print(recap.to_string(index=False))
        print()
        print(f"-> {out}  ({len(df_out)} lignes)")
        print("Pour information, pas d'assertion.")


if __name__ == "__main__":
    main()
