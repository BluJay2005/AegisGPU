# Fraud & AML Network Audit Report

**Request:** Synthesize suspicious transfer networks exceeding $10k routed through newly created accounts in the past 6 hours.  
**Data as of:** 2026-01-15 11:59:59  
**Planner:** rules  |  **Engine:** pandas + NetworkX (CPU fallback)

## Executive summary

In the analysis window ending 2026-01-15 11:59:59, 113 accounts crossed the risk threshold. The graph engine found 6 circular-flow network(s) ($281,621 cycled), 6 structuring cluster(s) ($598,065 moved in just-under-threshold transfers) and 10 additional new-account pass-through account(s).

## Key metrics

| Metric | Value |
|---|---|
| Accounts flagged (score ≥ 0.5) | 113 |
| Circular-flow networks | 6 ($281,621 principal) |
| Structuring clusters | 6 ($598,065) |
| Pass-through accounts | 10 |
| Cross-border ring hops | 39 |

## Circular money flows (layering)

| Ring | Accounts | Principal | Total flow | Duration | Countries | New accts | Transfers |
|---|---|---|---|---|---|---|---|
| RING-001 | `6558`, `13963`, `14772`, `15797` | $65,778 | $476,671 | 52 min | HK, PA, US | 4/4 | `194980`, `195172`, `195307`, `195714` … (+4) |
| RING-002 | `7763`, `12529`, `15080`, `16981` | $55,070 | $396,299 | 50 min | AE, CY, US | 4/4 | `196628`, `196980`, `197128`, `197652` … (+4) |
| RING-003 | `4280`, `16840`, `17116` | $51,669 | $292,819 | 42 min | CY, NG, US | 3/3 | `196505`, `196665`, `197328`, `197657` … (+2) |
| RING-004 | `4130`, `6549`, `10664`, `12127`, `13289`, `18864` | $49,046 | $271,869 | 49 min | AE, HK, NG | 6/6 | `196459`, `197112`, `197550`, `197904` … (+2) |
| RING-006 | `5454`, `5549`, `5991`, `7513`, `8460`, `19379` | $39,885 | $399,971 | 84 min | AE, CY, GB, HK, NG | 6/6 | `195088`, `195408`, `196027`, `196425` … (+8) |
| RING-008 | `3665`, `6133`, `17777`, `20479`, `20789` | $20,174 | $95,758 | 29 min | CY, GB, NG, US | 5/5 | `198092`, `198776`, `198896`, `199014` … (+1) |

## Structuring (just under the $10k threshold)

| Cluster | Hub | Direction | Counterparties | Total | Window | Forwarded to |
|---|---|---|---|---|---|---|
| STRUCT-001 | `14343` | fan_in | 15 | $142,869 | 07:24–08:22 | `3734` |
| STRUCT-002 | `18361` | fan_in | 14 | $131,944 | 08:48–09:45 | `20969` |
| STRUCT-003 | `19076` | fan_in | 9 | $85,388 | 10:08–10:52 | `1081` |
| STRUCT-004 | `13283` | fan_in | 9 | $84,951 | 08:36–09:22 | `15738` |
| STRUCT-005 | `13655` | fan_in | 8 | $76,962 | 08:05–08:57 | `1378` |
| STRUCT-006 | `18343` | fan_in | 8 | $75,950 | 10:23–11:15 | `18705` |

## New-account pass-through

| Account | Age (h) | In | Out | Forwarded after |
|---|---|---|---|---|
| `1440` | 1.3 | $29,616 | $27,491 | 13.7 min |
| `2414` | 2.6 | $17,701 | $17,094 | 55.9 min |
| `2510` | 4.9 | $26,171 | $25,129 | 60.7 min |
| `5854` | 4.4 | $43,620 | $41,965 | 86.3 min |
| `6004` | 1.1 | $35,385 | $32,310 | 53.5 min |
| `6049` | 1.8 | $32,161 | $31,524 | 63.2 min |
| `8649` | 2.2 | $16,743 | $15,448 | 65.8 min |
| `14705` | 0.7 | $23,643 | $22,418 | 46.4 min |
| `17479` | 0.5 | $43,885 | $39,763 | 88.1 min |
| `19253` | 3.5 | $15,086 | $14,744 | 29.5 min |

## Highest-risk accounts

| Account | Score | Why |
|---|---|---|
| `3665` | 0.75 | member of circular flow RING-008; ring spans multiple countries; account age 4.4h |
| `4130` | 0.75 | member of circular flow RING-004; ring spans multiple countries; account age 9.6h |
| `4280` | 0.75 | member of circular flow RING-003; ring spans multiple countries; account age 4.8h |
| `5454` | 0.75 | member of circular flow RING-006; ring spans multiple countries; account age 8.3h |
| `5549` | 0.75 | member of circular flow RING-006; ring spans multiple countries; account age 2.7h |
| `5991` | 0.75 | member of circular flow RING-006; ring spans multiple countries; account age 3.5h |
| `6133` | 0.75 | member of circular flow RING-008; ring spans multiple countries; account age 3.9h |
| `6549` | 0.75 | member of circular flow RING-004; ring spans multiple countries; account age 11.5h |
| `6558` | 0.75 | member of circular flow RING-001; ring spans multiple countries; account age 1.9h |
| `7513` | 0.75 | member of circular flow RING-006; ring spans multiple countries; account age 16.5h |

## Similar past investigations (pgvector semantic search)

- **ring** (similarity 0.302): Circular flow across 3 accounts: $56,000 cycled back to the origin in 97 minutes with roughly 5% shaved per hop. Accounts opened within 23 hours of activity. Confirmed layering, SAR filed.
- **ring** (similarity 0.27): Closed-loop transfers between 4 newly opened accounts, funds returned to origin after 109 minutes. Cross-border to CY. Escalated to FIU; accounts frozen.
- **structuring** (similarity 0.353): 6 new accounts each sent $9000-$9900 to one beneficiary within 3 hours, deliberately under the $10,000 reporting threshold. Beneficiary forwarded funds offshore (CY). Structuring confirmed.
- **structuring** (similarity 0.353): 14 new accounts each sent $9000-$9900 to one beneficiary within 24 hours, deliberately under the $10,000 reporting threshold. Beneficiary forwarded funds offshore (CY). Structuring confirmed.
- **mule** (similarity 0.312): Newly created account received $46,000 from an authorized push-payment scam victim and forwarded ~93% within 82 minutes. Funds recalled, account closed.
- **mule** (similarity 0.286): Newly created account received $81,000 from an authorized push-payment scam victim and forwarded ~96% within 43 minutes. Funds recalled, account closed.

## Recommended actions

1. Place temporary holds on all members of each circular-flow network; open a linked case and evaluate a SAR filing for layering.
2. Aggregate each structuring cluster's deposits for CTR/SAR analysis; review feeder-account onboarding (device, KYC data reuse) and follow forwarded funds to downstream beneficiaries.
3. Contact the receiving institutions to recall funds from pass-through accounts; check the sending accounts for authorized-push-payment scam victims.
4. Escalate cross-border legs for sanctions / high-risk-jurisdiction screening.

## Audit trail

Parameters: `{"min_amount": 10000.0, "lookback_hours": 6.0, "new_account_hours": 24, "require_new_account": true, "cycle_window_hours": 6.0, "max_cycle_len": 6, "min_cycle_len": 3, "retention": 0.75, "band_low": 9000, "band_high": 10000, "min_counterparties": 5, "min_structuring_total": 25000, "structuring_window_hours": 6.0, "mule_ratio": 0.85, "mule_max_delay_hours": 2, "flag_threshold": 0.5}`

```sql
WITH anchor AS (SELECT MAX(ts) AS now_ts FROM transactions) SELECT t.tx_id, t.src, t.dst, t.amount, t.ts, t.geo_lat, t.geo_lon, (julianday(anchor.now_ts) - julianday(sa.created_at)) * 24.0 AS src_age_h, (julianday(anchor.now_ts) - julianday(da.created_at)) * 24.0 AS dst_age_h FROM transactions t CROSS JOIN anchor JOIN accounts sa ON sa.account_id = t.src JOIN accounts da ON da.account_id = t.dst WHERE t.amount >= 9000 AND t.ts >= datetime(anchor.now_ts, '-6.0 hours') ORDER BY t.ts LIMIT 5000000
```

Timings (ms): `{"host_to_device": 0.59, "join_enrich_geo": 7.56, "cycles_scc_prune_plus_joins": 32.24, "structuring_groupby": 10.29, "pass_through_mules": 9.13, "pagerank": 99.09, "total": 181.56}` — 154 transfers in window.
