# Selected protocol: WHO Interagency Integrated Triage Tool (IITT)

## Selection and scope

Triage Sentinel selects the **routine triage** edition of the Interagency
Integrated Triage Tool (IITT), developed by the World Health Organization
(WHO), the International Committee of the Red Cross (ICRC), and Médecins Sans
Frontières (MSF). The official [WHO IITT page](https://www.who.int/tools/triage)
describes facility-based triage in emergency units using red (high acuity),
yellow (moderate acuity), and green (low acuity) categories.

This prototype is limited to clinician-supervised triage of unscheduled,
acutely ill or injured patients in an emergency-unit workflow. The adult
poster applies at age **12 years and above**; the pediatric poster applies
below age **12 years**. Mass-casualty triage is excluded.

The WHO FAQ says routine IITT is not generally recommended as the screening
approach in health posts where most people waiting are clinically stable and
only a small number need urgent identification. This project does not claim
that IITT is universally suitable for outpatient queues, that it is approved
by the Kenyan Ministry of Health, or that a facility has authorized its use.
The implementation is a competition prototype, not a clinically validated
or approved medical device.

## Source documents and provenance

The official source page links the following materials. They were retrieved
from those WHO-hosted URLs on **2026-10-15**. The local PDF names below refer
to temporary verification downloads; the PDFs are not redistributed in this
repository. SHA-256 values identify the exact downloaded bytes.

| Official material | Population / use | Version information visible in source | SHA-256 |
| --- | --- | --- | --- |
| [Interagency Integrated Triage Tool: Age ≥ 12](https://cdn.who.int/media/docs/default-source/integrated-health-services-%28ihs%29/csy/iitt/iitt_adult.pdf?sfvrsn=b2a91431_1) | Adults and older children, age ≥12; routine emergency-unit triage | One-page poster. PDF metadata title contains `20200326-v6`; creation/modification metadata is 2023-12-11. No separate publication date is printed. Local verification filename: `adult.pdf`. | `9597ab1f4f28687d9d2e1e28bfac6a84191889c6800198571b6af6a160e5711d` |
| [Interagency Integrated Triage Tool: Age < 12](https://cdn.who.int/media/docs/default-source/integrated-health-services-%28ihs%29/csy/iitt/iitt_pediatric.pdf?sfvrsn=15161bfb_1) | Children under 12; routine emergency-unit triage | One-page poster. PDF metadata title contains `20200326-v6`; creation/modification metadata is 2023-12-11. No separate publication date is printed. Local verification filename: `pediatric.pdf`. | `c1fd4004447c6511a999a96177d887ba3ddfbe7a00e4ff38ba5794dabff515c4` |
| [Interagency Integrated Triage: Reference card](https://cdn.who.int/media/docs/default-source/integrated-health-services-%28ihs%29/csy/iitt/iitt_reference-card09165e1c-0df5-472d-ab53-45506191a7bd.pdf?sfvrsn=eba17410_2) | High-risk signs referenced by both posters | One page; PDF metadata modification date 2025-11-20. Local verification filename: `reference-card.pdf`. | `d69bbe391901cbbbe14966df93af1f9024d5b9881705589f47dcb4df02a3ca7d1` |
| [IITT Frequently Asked Questions](https://cdn.who.int/media/docs/default-source/integrated-health-services-%28ihs%29/csy/iitt/iitt-faq.pdf?sfvrsn=cc11cf12_5) | Applicability, workflow, categories, and adaptation cautions | Three pages; PDF creation/modification metadata is 2023-12-18. Local verification filename: `faq.pdf`. | `8d3c38c94b36fdad8f5a0ed21dde08d8cbd3d4586cb9c7c11a14cf51e1e071eb` |

The adult and pediatric poster filenames and embedded metadata indicate a
2020-03-26 design/version string (`v6`), while the PDFs themselves were
created/reposted later. WHO does not identify a numbered edition or
publication date on the posters. `source-snapshot-2026-10-15/ruleset-0.1.0`
is this project's source snapshot and implemented-subset version, **not** a
WHO-issued protocol version.

## Copyright and reuse

The downloaded reference card explicitly states that it is © WHO, MSF, and
ICRC and is available under the
[Creative Commons Attribution-NonCommercial-ShareAlike 3.0 IGO licence
(CC BY-NC-SA 3.0 IGO)](https://creativecommons.org/licenses/by-nc-sa/3.0/igo/).
The WHO [copyright and licensing policy](https://www.who.int/about/policies/publishing/copyright)
says the applicable notice in each publication governs; materials not
available under that licence may require WHO permission, and commercial use
requires permission.

The adult poster, pediatric poster, and FAQ have no separate reuse notice
visible in the retrieved documents. The WHO page links them, but that alone
does not establish a licence for adaptation or redistribution. This
repository therefore retains links and hashes, not copies of the PDFs or
verbatim poster text. The evaluator contains only a limited, structured
encoding of selected criteria for a non-commercial prototype. Obtain
appropriate rights-holder/legal confirmation before commercial use,
redistribution, or broader adaptation; no unrestricted reuse right is
asserted here.

## Verified criteria encoded

Rule identifiers are stable project identifiers. Each finding links to the
supporting synthetic observation and records `age_months` as context evidence
for population/age-band selection. Source locations identify the one-page
poster and its displayed section; the complete source is at the linked URL
above. Comparators below preserve the explicit poster notation.
For pediatric high/low vital-sign cut-points, the poster prints “High” and
“Low” rows but omits comparator symbols at the high values and at the
respiratory-rate low values. Values exactly on any such cut-point are
therefore reported as indeterminate, not normal or abnormal.

| Rule IDs | Encoded condition | Result semantics | Source location |
| --- | --- | --- | --- |
| `IITT-ADULT-RED-HEART-RATE` | At age ≥12, heart rate <50 or >150 bpm | Criterion met: red category / immediate human escalation. | Adult poster, “Check for red criteria”, Circulation |
| `IITT-ADULT-HIGH-RISK-HEART-RATE` | At age ≥12, heart rate <60 or >130 bpm | Criterion met: immediate up-triage or supervising-clinician review; not an independently assigned yellow/green category. | Adult poster, “Check for high-risk vital signs” |
| `IITT-ADULT-HIGH-RISK-RESPIRATORY-RATE` | At age ≥12, respiratory rate <10 or >30 breaths/min | Same high-risk human-review semantics. | Adult poster, “Check for high-risk vital signs” |
| `IITT-ADULT-HIGH-RISK-TEMPERATURE` | At age ≥12, temperature <36°C or >39°C | Same high-risk human-review semantics. Fahrenheit input is converted exactly for comparison. | Adult poster, “Check for high-risk vital signs” |
| `IITT-ADULT-HIGH-RISK-OXYGEN-SATURATION` | At age ≥12, SpO₂ <92% | Same high-risk human-review semantics. | Adult poster, “Check for high-risk vital signs” |
| `IITT-ADULT-HIGH-RISK-AVPU` | At age ≥12, AVPU is other than A | Same high-risk human-review semantics. | Adult poster, “Check for high-risk vital signs” |
| `IITT-CHILD-HIGH-RISK-HEART-RATE` | Under 12: low cut-points <90, <80, <70 bpm for <1, 1–4, and 5–<12 years; high cut-points 180, 160, 140 bpm in those groups | Values definitively below the low cut-point or above the high cut-point require supervising-clinician review. Exact cut-points are indeterminate because high-row comparator is omitted. | Pediatric poster, “Check for high-risk vital signs”, HR table |
| `IITT-CHILD-HIGH-RISK-RESPIRATORY-RATE` | Under 12: low/high cut-points 25/50, 20/40, 10/30 breaths/min for <1, 1–4, and 5–<12 years | Values below the low cut-point or above the high cut-point require review. Exact cut-points are indeterminate because comparator symbols are omitted. | Pediatric poster, “Check for high-risk vital signs”, RR table |
| `IITT-CHILD-HIGH-RISK-TEMPERATURE` | Under 12, temperature <36°C or >39°C | High-risk human-review semantics. | Pediatric poster, “Check for high-risk vital signs” |
| `IITT-CHILD-HIGH-RISK-OXYGEN-SATURATION` | Under 12, SpO₂ <92% | High-risk human-review semantics. | Pediatric poster, “Check for high-risk vital signs” |
| `IITT-CHILD-HIGH-RISK-AVPU` | Under 12, AVPU is other than A | High-risk human-review semantics. | Pediatric poster, “Check for high-risk vital signs” |
| `IITT-CHILD-RED-TEMPERATURE-UNDER-2-MONTHS` | Under 12 and younger than 2 months, temperature <36°C or >39°C | Criterion met: red category / immediate human escalation. | Pediatric poster, “Check for red criteria”, Other |

The pediatric age bands above are evaluated in months: `<12`, `12–<60`, and
`60–<144`; exactly 144 months uses the adult poster. Age is supplied in
months, so the separate pediatric red criterion “any infant <8 days old” is
not implemented: this input model does not preserve exact infant age in
days.

## Explicitly unsupported / unassessed criteria

This evaluator does not parse presenting-complaint prose or encode the
remaining IITT red/yellow criteria, including respiratory distress, perfusion,
neurologic/other clinical signs, pregnancy, trauma and exposure criteria
from the reference card, or local facility workflows. It does not implement
yellow or green assignment. Unsupported criteria are returned in every
assessment; absent findings cannot be interpreted as normal, safe, or green.

Observation timestamps must be timezone-aware and cannot be after the
evaluation timestamp. The source does not define a data-freshness interval,
so this implementation does not label observations stale or invent a
staleness cutoff. Duplicate measurement types are rejected as ambiguous.
Missing observations are listed explicitly. Boundary ambiguities in
pediatric high/low rows are surfaced as indeterminate.

The evaluator returns findings only. It does not diagnose, prescribe,
discharge, admit, write a queue event, or authorize a clinician. A red result
or high-risk vital sign must not be delayed while waiting for this software.
Direct clinical assessment and the facility's emergency response remain
authoritative.
