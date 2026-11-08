import json, sys, os
import numpy as np

root = sys.argv[1]
seeds = [0, 1, 2, 3, 4]
L = lambda p: json.load(open(p, encoding='utf-8'))
R = lambda s, f: L(f"{root}/seed_{s:02d}/results/{f}.json")
out = {}

cvd = [R(s, 'centralized_vs_decentralized') for s in seeds]
e2e = [R(s, 'end_to_end_summary') for s in seeds]
ev = [R(s, 'event_metrics') for s in seeds]
st = [R(s, 'state_metrics') for s in seeds]
summ = L(f"{root}/summary.json")['metrics']

m = lambda xs: float(np.mean(xs))
out['central'] = {c: dict(events=cvd[0][c]['events'],
                          cen=100 * m([x[c]['centralized_accuracy'] for x in cvd]),
                          dec=100 * m([x[c]['decentralized_accuracy'] for x in cvd]),
                          touched=100 * m([x[c]['decentralized_gallery_touched'] for x in cvd]))
                  for c in ('all', 'local', 'visitor')}
for c in out['central'].values():
    c['gap'] = c['cen'] - c['dec']
out['events'] = {c: {k: m([x[c][k] for x in ev]) for k in ('tp', 'fp', 'fn', 'precision', 'recall', 'f1')}
                 for c in ('all', 'local', 'visitor')}
out['events_n'] = {c: ev[0][c]['items'] for c in ('all', 'local', 'visitor')}
out['n_labels'] = ev[0]['n_labels']
out['visitor'] = dict(
    routing_recall=100 * m([x['routing_recall'] for x in e2e]),
    per_routed=m([x['communication_cost_mean_candidates_queried'] for x in e2e]),
    per_visitor_event=m([x['communication_cost_per_visitor_event_incl_db_hits'] for x in e2e]),
    served_db=m([x['visitor_events_served_from_visitor_db'] for x in e2e]),
    pointer_prec=100 * m([x['pointer_precision']['precision'] for x in e2e]),
    routed_events=m([x['routed_visitor_events'] for x in e2e]),
)
out['seeds'] = [dict(seed=s, all=100 * cvd[i]['all']['decentralized_accuracy'], local=100 * cvd[i]['local']['decentralized_accuracy'],
                     visitor=100 * cvd[i]['visitor']['decentralized_accuracy'], routing=100 * e2e[i]['routing_recall'])
                for i, s in enumerate(seeds)]
ci = lambda k: summ[k]['ci95']
out['seed_ci'] = dict(all=100 * ci('events.accuracy'), local=100 * ci('events.local_accuracy'),
                      visitor=100 * ci('events.visitor_accuracy'), routing=100 * ci('events.routing_recall'))
out['seed_sd_all'] = 100 * summ['events.accuracy']['std']
out['state'] = {lv: dict(p=summ[f'state_confusion.{lv}.mean_precision']['mean'], r=summ[f'state_confusion.{lv}.mean_recall']['mean'],
                         f1=summ[f'state_confusion.{lv}.mean_f1']['mean'], vr=summ[f'state_confusion.{lv}.mean_visitor_recall']['mean'])
                for lv in ('building', 'zone', 'location')}
out['state_sd_building_f1'] = summ['state_confusion.building.mean_f1']['std']

fl = [R(s, 'explanation_flags') for s in seeds]
ov = [R(s, 'explanation_overhead') for s in seeds]
ident = {}
for g, path in (('all', None), ('local', 'local'), ('routed', 'routed'), ('db', 'present_visitor_pool')):
    rows = [(x['identifications'] if path is None else x['identifications']['by_mode'][path]) for x in fl]
    d = {k: m([r[k] for r in rows]) for k in ('identified_events', 'wrong_identifications')}
    # pooled shares over the five seeds average of per-seed shares
    for k in ('share_of_wrong_flagged', 'share_of_correct_flagged', 'share_of_flagged_that_are_wrong'):
        vals = [r[k] for r in rows if r[k] is not None]
        d[k] = 100 * m(vals) if vals else None
    ident[g] = d
out['flags_ident'] = ident
pf = {}
for name in fl[0]['identifications']['per_flag']:
    ev_ = sum(x['identifications']['per_flag'][name]['events'] for x in fl)
    wr = sum(x['identifications']['per_flag'][name]['wrong'] for x in fl)
    pf[name] = 100 * wr / ev_ if ev_ else None
out['per_flag'] = pf
ans = [x['answers']['overall'] for x in fl]
out['answers'] = dict(n=m([a['answers'] for a in ans]), share_flagged=100 * m([a['share_flagged'] for a in ans]),
                      wrong=m([a['wrong'] for a in ans]), recall=100 * m([a['recall'] for a in ans]),
                      precision=100 * m([a['precision'] for a in ans]))
tot = [x['totals'] for x in ov]
out['overhead'] = dict(lat_wo=m([t['latency_ms_without'] for t in tot]), lat_w=m([t['latency_ms_with'] for t in tot]),
                       pooled=100 * (m([t['latency_ms_with'] for t in tot]) / m([t['latency_ms_without'] for t in tot]) - 1),
                       pct_min=min(t['latency_overhead_pct'] for t in tot), pct_max=max(t['latency_overhead_pct'] for t in tot),
                       bytes_wo=m([t['json_bytes_without'] for t in tot]), bytes_w=m([t['json_bytes_with'] for t in tot]))
bl = []
for s in seeds:
    p = f"{root}/seed_{s:02d}/results/bloom_fpr.json"
    if os.path.exists(p):
        bl.append(L(p))
if bl:
    out['bloom'] = {k: 100 * m([b[k] for b in bl]) for k in ('effective_fpr', 'slot_collision_rate', 'random_integer_fpr', 'expected_slot_collision_if_uniform')}
out['n_buildings'] = cvd[0]['buildings']
json.dump(out, open(sys.argv[2], 'w'), indent=1)


def show(d, p=''):
    for k, v in d.items():
        if isinstance(v, dict):
            show(v, p + k + '.')
        elif isinstance(v, list):
            for r in v:
                print(p + k, {a: (round(b, 2) if isinstance(b, float) else b) for a, b in r.items()})
        else:
            print(f"{p}{k}: {round(v, 3) if isinstance(v, float) else v}")


show(out)
