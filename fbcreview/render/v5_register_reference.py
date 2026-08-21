# ── central register: drives markers, rail cards and the summary table ────
# fid, pg, anchor, hit, disc, status, sev, title, checked, result, code, body
R = [
# ══════════════ OPEN FINDINGS ══════════════
dict(fid="C-01", pg=16, anchor="OUTDOOR AIR CALCULATIONS", hit=0, disc="Mechanical",
 status="OPEN", sev="CRITICAL", title="Outdoor-air capacity 531 CFM below this sheet's own requirement",
 checked="Outdoor Air Calculations block recomputed line by line, then compared against the scheduled unit capacity in the Roof Top Unit Schedule and Air Balance table on the same sheet.",
 result="Required 881 CFM (Reception 20 + Mat Studio 860 + Closet 2 = 882, sheet says 881). RTU-2 scheduled Outside Air = 350 CFM. Shortfall 531 CFM — 40% of the requirement is delivered. The calculation is correct; the equipment selected cannot meet it, and no note reconciles the two figures.",
 code="FBC-M 403.3.1.1 · 403.3.1.1.1.1 · 405.1",
 action="Increase RTU-2 outdoor-air capacity, add a dedicated OA unit or ERV, or re-establish the ventilation occupant density with AHJ approval and recompute.",
 body=("THE MOST CONSEQUENTIAL FINDING IN THE SET. Recomputed line by line from this table.\n\n"
  "THE SHEET'S OWN CALCULATION:\n"
  "  Reception   164 SF @ 10/1000 -> 2 persons;  Rp 5 x 2 = 10;    Ra 0.06 x 164 = 9.8  =>  20 CFM\n"
  "  Mat Studio  994 SF @ 40/1000 -> 40 persons; Rp 20 x 40 = 800; Ra 0.06 x 994 = 59.6 => 860 CFM\n"
  "  Closet                                       Ra 0.12 x 13                          =>   2 CFM\n"
  "  TOTAL REQUIRED = 881 CFM (42 persons)   [recomputed 882 — rounding]\n\n"
  "SCHEDULED: RTU-2 — Supply 3000 / Return 2650 / OUTSIDE AIR 350 / Exhaust 0.\n\n"
  "SHORTFALL: 881 - 350 = 531 CFM. The unit delivers 40% of the outdoor air this sheet calculates.\n\n"
  "CODE: FBC-M 403.3.1.1 Outdoor Airflow Rate ('23); 403.3.1.1.1.1 Breathing Zone Outdoor Airflow ('23); "
  "405.1 General ('23).\n\n"
  "The arithmetic is correct — the requirement was computed properly and then a unit that cannot meet it was "
  "scheduled. No note on the sheet reconciles the two figures.\n\n"
  "ACTION: increase RTU-2 outdoor-air capacity, add a dedicated outdoor-air unit or ERV, or re-establish the "
  "ventilation occupant density with AHJ approval and recompute.")),

dict(fid="C-01b", pg=16, anchor="ROOF TOP UNIT SCHEDULE", hit=0, disc="Mechanical",
 status="OPEN", sev="CRITICAL", title="RTU-2 scheduled at 350 CFM outside air", checked="", result="", code="", action="", table=False,
 body=("See C-01 on this sheet. RTU-2: Supply 3000 / Return 2650 / Outside Air 350 / Exhaust 0. Required per the "
  "Outdoor Air Calculations block on this same sheet: 881 CFM.\n\n"
  "Two secondary items to confirm while this is open:\n"
  " - Supply air 3000 CFM exceeds 2000 CFM, which triggers duct smoke detection under FBC-M 606.2.1. An existing "
  "'SD - DETECTOR TO REMAIN' is shown; confirm it is listed for duct installation, located downstream of the "
  "filters, and interfaced to the fire alarm system.\n"
  " - Exhaust totals 210 CFM (3 x 70) against 350 CFM outdoor air, giving the '+140' net positive pressurization "
  "noted. Intentional and appropriate; unaffected by C-01.")),

dict(fid="C-01c", pg=16, anchor="AIR BALANCE", hit=0, disc="Mechanical",
 status="OPEN", sev="CRITICAL", title="Air Balance confirms the same 350 CFM", checked="", result="", code="", action="", table=False,
 body=("AIR BALANCE: RTU-2 Supply 3000 / Return 2650 / OUTSIDE AIR 350 / Exhaust 0; EF-1/2/3 at 70 CFM each; "
  "totals 3000 / 2650 / 350 / 210; building pressure +140.\n\nThe 350 CFM outside-air value appears here and in "
  "the Roof Top Unit Schedule, and is 531 CFM below the 881 CFM computed on this same sheet. See C-01.")),

dict(fid="H-01", pg=1, anchor="OCCUPANCY LOAD - FBC", hit=0, disc="Occupancy",
 status="OPEN", sev="HIGH", title="Mat Studio 102 carries three different occupant densities",
 checked="Occupant load factor applied to Mat Studio 102 (994 SF) on every sheet that assigns one, compared against the function list in FBC-B Table 1004.5.",
 result="G-1 egress uses 15 SF net (67 occ). The FFPC block on the same sheet is headed 'Exercise rooms without equipment'. M-1 ventilation uses 40 persons/1000 SF (40 occ). Table 1004.5 separately lists 'Exercise rooms' at 50 gross (20 occ), which is not applied anywhere. Three classifications for one room, reconciled on no sheet.",
 code="FBC-B 1004.5 · Table 1004.5",
 action="State one governing occupant load with its Table 1004.5 basis, state the ventilation density separately with its own basis, and cross-reference the two on G-1 rather than only on M-1.",
 body=("Mat Studio 102 (994 SF) is assigned a different density on every sheet that touches it:\n\n"
  "  G-1 EGRESS       15 SF NET  (1 per 15)      = 67 occupants\n"
  "  M-1 VENTILATION  40 per 1000 SF (1 per 25)  = 40 occupants\n"
  "  Table 1004.5 'Exercise rooms' 50 gross      = 20 occupants  (not applied)\n\n"
  "Table 1004.5 lists BOTH 'Assembly without fixed seats — Unconcentrated (tables and chairs) 15 net' AND "
  "'Exercise rooms 50 gross'. A mat pilates studio has no tables and chairs, and 'Exercise rooms' is expressly "
  "listed — so the 1004.5 fallback for unlisted functions does not apply.\n\n"
  "At 50 gross the total occupant load is 23, not 70 — changing exit count, egress width, fixture counts and risk "
  "category. M-1 carries a partial reconciliation ('DAILY BUSINESS OCCUPANCY LIMITED TO 42 OCCUPANTS...') but it "
  "sits on the mechanical sheet only and does not address the exercise-room factor.\n\n"
  "The 15-net choice is CONSERVATIVE for egress and therefore not unsafe — but it is not consistent, and the "
  "inconsistency is what a plans examiner will question.")),

dict(fid="H-02", pg=0, anchor="COMMON PATH OF TRAVEL", hit=0, disc="Means of egress",
 status="OPEN", sev="HIGH", title="Common path requirement understated on G-0 and contradicts G-1",
 checked="The common path of egress travel requirement stated in the G-0 egress table, against Table 1006.2.1 and against the same value stated on G-1.",
 result="G-0 states 50 LF required. Table 1006.2.1, row A/E/M, with sprinkler system, is 75 feet. G-1's FFPC block correctly states 75 LF. Provided is 8'-1\", so there is no physical deficiency — but the stated requirement is wrong and the two general sheets disagree.",
 code="FBC-B 1006.2.1 · Table 1006.2.1",
 action="Change 50 LF to 75 LF on G-0 so the two general sheets agree.",
 body=("STATED (G-0): required 50 LF, provided 8'-1\".\nSTATED (G-1, FFPC block): MAX. COMMON PATH 75 LF.\n"
  "CODE: FBC-B Table 1006.2.1, row 'A, E, M', WITH SPRINKLER SYSTEM = 75 feet.\n\n"
  "The 50 LF value has no basis in Table 1006.2.1 and disagrees with the other general sheet in the same set. "
  "No physical deficiency — but a wrong number in a code data block is what a plans examiner circles, and two "
  "general sheets disagreeing invites a broader look at the analysis.")),

dict(fid="M-01", pg=0, anchor="EXIT WIDTH REQUIRED", hit=0, disc="Means of egress",
 status="OPEN", sev="MEDIUM", title="0.15 in/occupant egress factor is not supported by the documents",
 checked="The means-of-egress capacity factor used to derive the required exit width, against the two conditions 1005.3.2 Exception 1 places on it, and against what the drawings actually show.",
 result="10.50\" required is 70 occupants x 0.15. The 0.15 factor requires sprinklers AND an emergency voice/alarm communication system; the set shows sprinklers, a pull station and a strobe, but no EVACS on G-1, E-1, E-2 or E-3. At the base 0.20 factor the requirement is 14.0\". Provided is 108\" either way.",
 code="FBC-B 1005.3.2 Exception 1 · 907.5.2.2",
 action="Document the emergency voice/alarm communication system, or recompute the required width at 0.20 in/occupant.",
 body=("STATED: 10.50\" required (70 x 0.15), 108.00\" provided.\n"
  "CODE: FBC-B 1005.3.2 Exception 1 — 0.15 applies only in buildings equipped throughout with an automatic "
  "sprinkler system per 903.3.1.1 or 903.3.1.2 AND an emergency voice/alarm communication system per 907.5.2.2. "
  "Both conditions are required.\n\nThe set shows sprinklers, a fire alarm pull station and a strobe. No EVACS "
  "appears on G-1, E-1, E-2 or E-3.\n\nAt the base factor: 70 x 0.20 = 14.0 inches. Provided 108\" either way, so "
  "there is no physical deficiency — but the stated requirement is understated.")),

dict(fid="M-02", pg=1, anchor="OCCUPANT CAPACITY", hit=0, disc="Means of egress",
 status="OPEN", sev="MEDIUM", title="Two different capacity factors used in one analysis",
 checked="The capacity factor used to derive exit discharge capacities, against the factor used for required width on the same sheet.",
 result="Exit Discharge 1 (72\") is rated 360 occupants and Exit Discharge 2 (36\") is rated 180 — both at 0.20 in/occupant. Required width on the same sheet uses 0.15. The capacities were computed the conservative way and the requirement the permissive way.",
 code="FBC-B 1005.3.2",
 action="Resolve M-01 first, then apply the chosen factor uniformly across the sheet.",
 body=("Exit Discharge 1: 72\" provided, capacity 360 = 72 / 0.20.\nExit Discharge 2: 36\" provided, capacity 180 = 36 / 0.20.\n"
  "Required width elsewhere on this sheet: 10.50\" = 70 x 0.15.\n\nFBC-B 1005.3.2 supports one factor or the "
  "other, not both in the same analysis.")),

dict(fid="M-03", pg=0, anchor="RISK CATEGORY", hit=0, disc="Structural",
 status="OPEN", sev="MEDIUM", title="Risk Category III does not match the stated occupant load",
 checked="The risk category on the G-0 project data block against the occupant-load thresholds in Table 1604.5.",
 result="Risk Category III is assigned. Table 1604.5 places public assembly in Risk Category III only where the occupant load exceeds 300. The stated load is 70 (23 if H-01 resolves to the exercise-room factor). Risk Category II applies. RC III is conservative — it raises design wind pressures — and harmless on a Level II alteration with no structural scope, but it contradicts the occupant load on the same sheet.",
 code="FBC-B Table 1604.5",
 action="Confirm Risk Category II, or state the basis for III (for example, inherited from the base-building shell design).",
 body=("STATED: Risk Category III.\nCODE: FBC-B Table 1604.5 — RC III covers buildings 'whose primary occupancy is "
  "public assembly with an occupant load greater than 300.' RC II is everything not in I, III or IV.\n\n"
  "Stated occupant load is 70 — 23 if the exercise-room factor in H-01 governs. Either is far below 300.")),

dict(fid="M-04", pg=0, anchor="1,436 SF", hit=0, disc="Existing building",
 status="OPEN", sev="MEDIUM", title="Building area disagrees between sheets; Level II work area not identified",
 checked="Building area stated on G-0 against the area totals on G-1, and the Level II alteration scope against the work-area provisions of the Existing Building code.",
 result="G-0 states 1,436 SF; G-1's occupancy tables total 1,375 SF — 61 SF apart, with no gross/net basis stated. Table 1004.5 factors are basis-specific, so the occupant load cannot be independently checked without it. Separately, the set states 'ALTERATION - LEVEL II' but shows neither a work-area boundary nor its percentage of building area.",
 code="FBC-EBC 601.2 · 603.1 · FBC-B Table 1004.5",
 action="Label both areas with their basis, and add a work-area boundary with its percentage of the building area.",
 body=("G-0: BUILDING AREA 1,436 SF. G-1 occupancy tables: TOTAL 1,375 SF. Difference 61 SF (4.2%).\n"
  "Most likely gross vs net, but Table 1004.5 factors are basis-specific, so the basis must be explicit.\n\n"
  "RELATED: FBC-EBC 601.2 requires the work area on the construction documents; 603.1 conditions Level II scope "
  "on it being 50 percent or less of the building area. Neither the boundary nor the percentage is shown.")),

dict(fid="M-05", pg=1, anchor="MAT STUDIO", hit=0, disc="Means of egress",
 status="OPEN", sev="MEDIUM", title="Assembly occupant-load posting is not shown anywhere in the set",
 checked="Every sheet in the set for the permanent occupant-load sign required in an assembly space.",
 result="Mat Studio 102 is an assembly space with a design occupant load of 67. No occupant-load sign appears in the G-1 symbol legend, on A-2, or in the interior elevations on A-7 or A-8.",
 code="FBC-B 1004.9",
 action="Show the sign location near the main exit access doorway and note it as a permanent approved sign.",
 body=("Mat Studio 102 is an assembly space with a design occupant load of 67.\n\n"
  "CODE: FBC-B 1004.9 — 'Every room or space that is an assembly occupancy shall have the occupant load of the "
  "room or space posted in a conspicuous place, near the main exit or exit access doorway from the room or space... "
  "Posted signs shall be of an approved legible permanent design.'")),

dict(fid="M-06", pg=7, anchor="FLAME-SPREAD", hit=0, disc="Interior finishes",
 status="OPEN", sev="MEDIUM", title="Interior finish classes are called for but not stated",
 checked="The finish schedule and finish notes on A-4 for the flame-spread / smoke-developed classification of each finish.",
 result="General note 5 requires flame-spread and smoke-development ratings to be provided, but no Class A/B/C designation is assigned to any finish in the schedule. In a sprinklered Group A-3, Table 803.13 sets the permitted classes by space type — the reviewer cannot confirm compliance without the classes on the sheet.",
 code="FBC-B Table 803.13 · 803.1.1",
 action="Add the Class A/B/C designation to each finish in the schedule, or add a note assigning classes by space.",
 body=("A-4 General Note 5 requires flame-spread and smoke-development ratings to be provided for interior finishes, "
  "but the finish schedule assigns no Class A/B/C designation.\n\n"
  "CODE: FBC-B Table 803.13 sets permitted interior wall and ceiling finish classes by occupancy and space type, "
  "with separate columns for sprinklered buildings; 803.1.1 governs classification by test.\n\n"
  "This is a documentation gap rather than a demonstrated deficiency — the finishes may well comply. It is the "
  "kind of item that generates a plan-review comment because the reviewer cannot confirm it from the sheet.")),

dict(fid="M-07", pg=12, anchor="RECEPTION COUNTER", hit=0, disc="Accessibility",
 status="OPEN", sev="MEDIUM", title="Accessible portion of the reception counter is not dimensioned",
 checked="The reception counter detail on A-9 for a designated accessible section and its height above finished floor.",
 result="The detail shows a marble countertop with an internal mounting bracket but does not identify which portion of the counter serves as the accessible sales/service counter, nor dimension its height. A sales or service counter serving the public requires an accessible portion no higher than 36 inches with a 36-inch minimum length.",
 code="FBC-Accessibility 904.4 · 904.4.1",
 action="Identify the accessible section of the counter on the plan and dimension its top at 36 in. maximum above finished floor for a length of at least 36 in.",
 body=("The A-9 reception counter detail shows construction but does not identify or dimension the accessible "
  "portion.\n\nFBC-Accessibility 904.4 requires sales and service counters to have an accessible portion; 904.4.1 "
  "(parallel approach) permits a counter 36 in. maximum above the floor with a 36 in. minimum length of "
  "accessible counter surface.\n\nA-5 does not mark which counter segment is the accessible one either. This is "
  "one of the most commonly-cited accessibility comments on a tenant improvement of this type.")),

dict(fid="L-01", pg=1, anchor="HALL, RESTROOMS", hit=0, disc="Occupancy",
 status="OPEN", sev="LOW", title="'ACCESSORY / 0' is not a Table 1004.5 function",
 checked="The occupant load factor column of the G-1 occupancy tables against the function list in Table 1004.5.",
 result="Hall and restrooms are shown as 'ACCESSORY' with a factor of 0. Table 1004.5 has no accessory function with a zero factor. Assigning zero to circulation and toilet rooms is accepted practice under 1004.2.1 — those occupants are counted in the spaces they serve — but a zero factor inside a column headed 'Table 1004.5' invites a comment.",
 code="FBC-B Table 1004.5 · 1004.2.1",
 action="Footnote the row to 1004.2.1 rather than presenting it as a Table 1004.5 factor.",
 body=("STATED: 'HALL, RESTROOMS | ACCESSORY | 204 SF | 0 | 0'.\n\nTable 1004.5 has no 'accessory' function with a "
  "zero factor; the nearest entry is 'Accessory storage areas, mechanical equipment room = 300 gross.'\n\n"
  "The practice is accepted under 1004.2.1 (cumulative occupant loads through intervening spaces) — it just "
  "should be cited to that section.")),

dict(fid="L-02", pg=16, anchor="DAILY BUSINESS OCCUPANCY", hit=0, disc="Mechanical",
 status="OPEN", sev="LOW", title="Ventilation row label does not match the area it was computed from",
 checked="Each row of the Outdoor Air Calculations block recomputed against the room areas it names.",
 result="The row totalling 2 CFM computes as 0.12 CFM/SF x 13 SF, which is Closet 104 — not the 204 SF of hall and restrooms it appears to describe (which would be 24 CFM). Restrooms are normally exhaust-only and carry no supply outdoor air, so the result is likely correct; only the label is wrong. C-01's 881 CFM total is unaffected.",
 code="FBC-M 403.3.1.1",
 action="Relabel the row to match the space it represents.",
 body=("The third row of the Outdoor Air Calculations block shows Ra = 0.12 CFM/SF and a total of 2 CFM.\n\n"
  "0.12 x 13 SF (Closet 104) = 1.6 -> 2 CFM.  0.12 x 204 SF (Hall + Restrooms) would be 24 CFM.\n\n"
  "Label mismatch only. This does not change C-01: 881 CFM stands either way.")),

# ══════════════ NEW IN v5 — OPEN ══════════════
dict(fid="H-03", pg=5, anchor="DOOR AND FRAME SCHEDULE", hit=0, disc="Means of egress / Accessibility",
 status="OPEN", sev="HIGH", title="Door 104 is a 2'-8\" leaf — under the 32 in. clear width the set itself requires",
 checked="Every leaf width in the A-2 Door and Frame Schedule against the 32 in. minimum clear opening in FBC-B 1010.1.1, and against the door tag locations on the proposed plan.",
 result="Door 104 is scheduled as a new Type A single-panel solid core wood door, 2'-8\" x 7'-0\" x 1-3/4\", and its tag sits at the entry to Unisex Restroom 106. A 2'-8\" (32 in.) leaf produces roughly 30-1/8 in. of clear width at 90 degrees once the 1-3/4 in. leaf and stop are deducted — about 2 in. short. G-0's own code data block states 32 in. clear opening required and provided, and G-3's own door detail is drawn as '36\" MIN DOOR / 32\" MIN. CLR.'",
 code="FBC-B 1010.1.1 · FBC-A 404.2.3 · FBC-A 213.2 Exc. 4",
 action="Change Door 104 to a 3'-0\" leaf, matching Door 103, or demonstrate 32 in. of net clear width with the specified leaf and hardware.",
 body=("SCHEDULED: 104 — Style A (single panel solid core wood, hollow metal frame), 2'-8\" x 7'-0\" x 1-3/4\", "
  "Hardware Set 1, no 'existing' remark. The tag for door 104 sits at Unisex Restroom 106 on the proposed plan.\n\n"
  "CLEAR WIDTH: a 2'-8\" (32 in.) leaf swung to 90 degrees gives roughly 32 - 1.75 (leaf) - ~0.125 (stop) = "
  "30-1/8 in. clear. Required 32 in.\n\n"
  "CODE: FBC-B 1010.1.1 — egress doors shall provide a clear width of not less than 32 inches, measured from the "
  "face of the door to the stop with the door open 90 degrees. The listed exceptions cover Group I-3 sleeping "
  "units, revolving/ power-operated doors, and storage closets LESS THAN 10 square feet. None applies to a "
  "toilet room.\n\n"
  "THE SET CONTRADICTS ITSELF ON THIS POINT:\n"
  " - G-0 code data block: 'CLEAR OPENING WIDTH ... 32 in.' required and provided.\n"
  " - G-3 'DOORWAY APPROACH REQUIREMENTS' detail: drawn as 36\" MIN DOOR yielding 32\" MIN. CLR.\n"
  " - Door 103, serving the accessible restroom 105, is correctly scheduled at 3'-0\".\n\n"
  "ACCESSIBILITY: if Restroom 106 is the non-accessible half of the two-room cluster under FBC-A 213.2 "
  "Exception 4, FBC-A 404.2.3 does not reach it — but FBC-B 1010.1.1 still does, and it has no small-toilet-room "
  "exception. The fix is the same either way.\n\n"
  "SECONDARY — Door 105, the 2'-8\" bifold louver door to Closet 104: 1010.1.1's storage-closet exception applies "
  "only below 10 square feet. Confirm Closet 104's area; the M-1 ventilation table computes it at about 13 SF.\n\n"
  "ACTION: change Door 104 to a 3'-0\" leaf to match Door 103.")),

dict(fid="M-08", pg=21, anchor="FLOOR DRAIN", hit=0, disc="Plumbing",
 status="OPEN", sev="MEDIUM", title="Trap seal protection not shown at the two restroom floor drains",
 checked="P-1 plan, P-1 plumbing legend, and the P-3 sanitary riser for a trap seal primer valve or listed barrier-type device at each floor drain.",
 result="Two floor drains (FD) are shown, one in each restroom, and both appear again on the P-3 riser. No trap primer, primer line, or barrier-type trap seal device appears in the legend, on the plan, or in the riser. A restroom floor drain in a studio with no wet program is a trap seal subject to evaporation.",
 code="FBC-P 1002.4.1",
 action="Show a potable-water trap seal primer valve, a listed barrier-type trap seal protection device, or a waste-water-fed primer at each floor drain, and add it to the legend.",
 body=("Two floor drains are shown — FD in Restroom 105 and FD in Restroom 106 — on P-1 and again on the P-3 "
  "sanitary riser diagram.\n\n"
  "CODE: FBC-P 1002.4.1 Trap Seal Protection — 'Trap seals of emergency floor drain traps and trap seals subject "
  "to evaporation shall be protected by one of the methods in Sections 1002.4.1.1 through 1002.4.1.4' "
  "(potable water-supplied trap seal primer valve, reclaimed or gray water primer, barrier-type trap seal "
  "protection device, or an approved system).\n\n"
  "Nothing of the kind appears in the plumbing legend, on the plan, or in the riser.\n\n"
  "This is a routine, inexpensive fix at design stage and one of the more commonly written plumbing plan-review "
  "comments on tenant improvements. It is far cheaper to add now than to chase after rough-in.")),

# ══════════════ VERIFIED — checked and found sufficient ══════════════
# ---- G-0 ----
dict(fid="V-01", pg=0, anchor="MAX TRAVEL DISTANCE", hit=0, disc="Means of egress",
 status="PASS", sev="VERIFIED", title="Travel distance limit — correct, and independently measured",
 checked="The 250 LF limit stated on G-0 against FBC-B Table 1017.2, and the 69'-4\" provided distance against the egress path geometry traced off G-1.",
 result="Table 1017.2, Occupancy A with a sprinkler system = 250 feet. Correct. Measured 68.9 ft against the 69'-4\" stated — within about 5 inches.",
 code="FBC-B Table 1017.2", action="None.",
 body=("STATED 250 LF required; 69'-4\" provided.\n\nCODE: FBC-B Table 1017.2 — Group A, with sprinkler system = "
  "250 feet. Value and citation both correct.\n\nMEASURED, not read: 68.9 ft off this file's own vector geometry. "
  "See the traced overlay on G-1.")),

dict(fid="V-02", pg=0, anchor="DEAD END CORRIDOR", hit=0, disc="Means of egress",
 status="PASS", sev="VERIFIED", title="Dead-end limit 20 LF — correct for Group A",
 checked="The 20 LF dead-end limit against FBC-B 1020.5 and its sprinklered exception list.",
 result="1020.5 sets 20 feet. The 50-foot sprinklered exception covers Groups B, E, F, I-1, M, R-1, R-2, S and U only — Group A is not in the list, so 20 feet governs. Correct.",
 code="FBC-B 1020.5", action="Confirm Hall 103 does not itself form a dead end over 20 ft.",
 body=("CODE: FBC-B 1020.5 — 20 feet. The 50-ft sprinklered exception covers Groups B, E, F, I-1, M, R-1, R-2, S "
  "and U; Group A is not included.\n\nCitation and value both correct — this is one that is frequently gotten "
  "wrong by applying the 50-ft exception to an assembly space.")),

dict(fid="V-03", pg=0, anchor="MIN. CORRIDOR WIDTH", hit=0, disc="Means of egress",
 status="PASS", sev="VERIFIED", title="Corridor width 44 in. and the 1020.3 citation are both correct",
 checked="The 44 in. minimum corridor width and the section number cited beside it against the 2023 FBC text.",
 result="Table 1020.3, 'Any facilities not listed below' = 44 inches. In the 2023 FBC / 2021 IBC the width requirement is at 1020.3 — older editions number it differently. Both the value and the citation are right.",
 code="FBC-B 1020.3 · Table 1020.3", action="None.",
 body=("CODE: FBC-B 1020.3 'Width and Capacity', Table 1020.3 — 'Any facilities not listed below' = 44 inches.\n\n"
  "Worth flagging as correct because it is easy to mis-cite: in the 2023 FBC the order is 1020.1 General, "
  "1020.2 Construction, 1020.3 Width and Capacity, 1020.4 Obstruction, 1020.5 Dead Ends. Both citations on this "
  "sheet check out against the current text.")),

dict(fid="V-04", pg=0, anchor="NUMBER OF EXITS", hit=0, disc="Means of egress",
 status="PASS", sev="VERIFIED", title="Two exits required and provided; separation passes with a large margin",
 checked="Exit count against Table 1006.3.2 for the stated occupant load, and the 71'-9\" exit separation against the 74'-11\" diagonal under 1007.1.1.",
 result="Table 1006.3.2 requires 2 exits for an occupant load of 1-500 per story; 2 are provided. Separation required is one-third of the diagonal where sprinklered (1007.1.1 Exception 2) = 24'-12\"; 71'-9\" is provided.",
 code="FBC-B Table 1006.3.2 · 1007.1.1 Exc. 2", action="None.",
 body=("Exit count: Table 1006.3.2, occupant load 1-500 per story = 2 exits. Two are provided.\n\n"
  "Separation: 1007.1.1 requires half the diagonal, reduced to one-third where the building is sprinklered "
  "(Exception 2). Diagonal 74'-11\" -> 24'-12\" required. Provided 71'-9\". Large margin — this holds even if "
  "the occupant-load question in H-01 resolves the other way.")),

dict(fid="V-05", pg=0, anchor="CLEAR OPENING WIDTH", hit=0, disc="Means of egress",
 status="PASS", sev="VERIFIED", title="32 in. clear opening is the correct requirement — see H-03 for whether it is met",
 checked="The 32 in. clear opening value stated in the G-0 code data block against FBC-B 1010.1.1 and FBC-A 404.2.3.",
 result="32 in. minimum clear width measured face-of-door to stop at 90 degrees, 80 in. minimum clear height. Both codes agree on 32 in. The requirement as stated is right; the A-2 door schedule does not meet it at Door 104 — see H-03.",
 code="FBC-B 1010.1.1 · FBC-A 404.2.3", action="Reconcile with the A-2 door schedule — see H-03.",
 body=("CODE: FBC-B 1010.1.1 — 32 in. minimum clear width measured from the face of the door to the stop with the "
  "door open 90 degrees; 80 in. minimum clear height. FBC-Accessibility 404.2.3 sets the same 32 in.\n\n"
  "The value stated on G-0 is correct. Zero tolerance, though — the scheduled 2'-8\" leaf at Door 104 does not "
  "produce it. See finding H-03 on A-2.")),

# ---- G-1 ----
dict(fid="V-06", pg=1, anchor="PLUMBING COUNTS", hit=0, disc="Plumbing",
 status="PASS", sev="VERIFIED", title="Every plumbing ratio, the 50/50 split and all round-ups are correct",
 checked="Each fixture ratio on G-1 against the Assembly row of FBC-B Table 2902.1, the occupant-load split under 2902.1.1, and the single-user provision at 2902.2.",
 result="Stated WC 1/125 male and 1/65 female, LAV 1/200, DF 1/500, 1 service sink — matches the Table 2902.1 row for 'Auditoriums without permanent seating, art galleries, exhibition halls, museums, lecture halls, libraries, arcades and gymnasiums' exactly. Occupant load 70 split 35/35 with fractions rounded up, per 2902.1.1. Provided: 2 unisex WC, 2 unisex LAV, 1 DF, 1 mop sink. Robust to the occupant-load question — at 23 occupants every ratio still rounds to 1.",
 code="FBC-B Table 2902.1 · 2902.1.1 · 2902.2", action="None.",
 body=("STATED: WC 1 per 125 male / 1 per 65 female; LAV 1 per 200; DF 1 per 500; 1 service sink. OL 70 -> 35/35.\n"
  "PROVIDED: 2 unisex WC, 2 unisex LAV, 1 DF, 1 service sink (mop sink on P-1).\n\n"
  "CODE CHECK:\n"
  " - Table 2902.1, Assembly row 'Auditoriums without permanent seating, art galleries, exhibition halls, museums, "
  "lecture halls, libraries, arcades and gymnasiums' — every ratio matches exactly.\n"
  " - 2902.1.1 — divide the occupant load in half, round fractions UP. The 35/35 split and every round-up correct.\n"
  " - 2902.2 — the single-user toilet room provision cited on the sheet is correctly applied.\n\n"
  "ROBUST TO H-01: at an occupant load of 23 every ratio still rounds to 1. This section of the analysis does not "
  "change whichever occupant load governs.")),

dict(fid="V-07", pg=1, anchor="TRAVEL DISTANCE", hit=0, disc="Means of egress",
 status="PASS", sev="MEASURED", title="Three egress paths and the egress band traced off the drawing's own geometry",
 checked="Travel Path 1, Travel Path 2, the common path of travel and the egress band width, each traced from the vector geometry on the AutoCAD layer 'Life Safety|Egress Path' and converted at the scale recorded in the PDF itself.",
 result="Path 1 measured 68.9 ft against 69'-4\" stated. Path 2 measured 25.6 ft against 24'-7\". Common path measured 9.5 ft against 8'-1\". Egress band measured 3'-8\" — exact. All four are far inside their limits, so none is a deficiency; they are reported because a review that only re-reads the designer's own labels has verified nothing.",
 code="Scale from the PDF /Measure dictionary — 18.005 pt per foot (1/4\" = 1'-0\")", action="None.",
 body=("Four measurements taken off this file's own vector geometry, not read off a label:\n\n"
  "  Travel Path 1   stated 69'-4\"  measured 68.9 ft   delta ~5 in    limit 250 ft\n"
  "  Travel Path 2   stated 24'-7\"  measured 25.6 ft   delta ~12 in   limit 250 ft\n"
  "  Common path     stated 8'-1\"   measured 9.5 ft    delta ~17 in   limit 75 ft\n"
  "  Egress band     stated 3'-8\"   measured 3'-8\"     exact          44 in min\n\n"
  "HOW THE SCALE WAS OBTAINED: each page carries an ISO 32000 /VP viewport array whose /Measure dictionaries hold "
  "exact conversion factors. G-1 carries 125 of them; the one governing the life-safety plan gives C = 0.05554 ft "
  "per point = 18.005 points per foot (1/4\" = 1'-0\").\n\n"
  "The deltas are measurement, not error — annotated distances begin at POINT A1, whose exact coordinate is a "
  "drafting decision, and the traced centreline is derived from the band edges.")),

# ---- G-2 ----
dict(fid="V-08", pg=2, anchor="ADA SPACE ALLOWANCES", hit=0, disc="Accessibility",
 status="PASS", sev="VERIFIED", title="Turning space, clear floor space, alcoves and all four reach ranges are correct",
 checked="Every dimension in the space-allowance and reach-range details against FBC-Accessibility 304.3, 305.3, 305.7, 307.2, 308.2 and 308.3.",
 result="60 in. turning circle and T-shaped space (304.3.1/304.3.2); 30 x 48 in. clear floor space (305.3); alcove rules 60 in. where depth exceeds 15 in. parallel and 36 in. where depth exceeds 24 in. forward (305.7); protruding objects between 27 in. and 80 in. limited to 4 in. (307.2); unobstructed reach 48 in. and forward-obstructed 48 in. at 20 in. deep dropping to 44 in. at 20-25 in. (308.2.2); side-obstructed 48 in. at 10 in. deep dropping to 46 in. at 10-24 in. (308.3.2). Every value matches.",
 code="FBC-A 304.3 · 305.3 · 305.7 · 307.2 · 308.2.2 · 308.3.2", action="None.",
 body=("Checked value by value against the FBC-Accessibility text:\n\n"
  " 304.3.1 turning circle 60 in.               drawing: 60\" CLEAR TURNING DIAMETER   OK\n"
  " 304.3.2 T-shaped turning space              drawing: T-SHAPED SPACE 60\"/36\"/12\"   OK\n"
  " 305.3  clear floor space 30 x 48 in.        drawing: 30\" MIN x 48\" MIN            OK\n"
  " 305.7  alcove, parallel, depth > 15 in.     drawing: X>15 -> 60\" MIN              OK\n"
  " 305.7  alcove, forward, depth > 24 in.      drawing: X>24 -> 36\" MIN              OK\n"
  " 307.2  protruding objects 27-80 in. AFF     drawing: X>27, X>80, 4\" MAX           OK\n"
  " 308.2.1 unobstructed forward reach 48 in.   drawing: 48\" MAX / 15\" MIN            OK\n"
  " 308.2.2 obstructed forward, <=20 in. deep   drawing: 48\" MAX / 20\" MAX            OK\n"
  " 308.2.2 obstructed forward, 20-25 in. deep  drawing: 44\" MAX / 20\"-25\" MAX        OK\n"
  " 308.3.1 unobstructed side reach 48 in.      drawing: 48\" MAX                      OK\n"
  " 308.3.2 obstructed side, <=10 in. deep      drawing: 48\" MAX / 10\" MAX            OK\n"
  " 308.3.2 obstructed side, 10-24 in. deep     drawing: 46\" MAX / 10\"-24\"            OK\n\n"
  "The 46 in. figure is the one most often drawn wrong. It is right here.")),

dict(fid="V-09", pg=2, anchor="ACCESSIBLE WATER CLOSETS", hit=0, disc="Accessibility",
 status="PASS", sev="VERIFIED", title="Water closet clearances, seat height, grab bars and dispenser location all match",
 checked="Every dimension in the two water-closet details against FBC-Accessibility 604.2 through 604.7.",
 result="Centerline 18 in. from the side wall (604.2 permits 16-18 in. — this is at the maximum, so it is compliant but has no tolerance to lose in the field); clearance 60 in. side x 56 in. rear (604.3.1); seat 17-19 in. (604.4); side grab bar 42 in. min, 12 in. max from the rear wall, extending 54 in. min (604.5.1); rear grab bar 36 in. min with 12 in. and 24 in. legs (604.5.2); grab bars 33-36 in. AFF (609.4); paper dispenser 7-9 in. in front of the bowl, 15-48 in. AFF (604.7).",
 code="FBC-A 604.2 · 604.3.1 · 604.4 · 604.5.1 · 604.5.2 · 604.7 · 609.4", action="Hold the 18 in. centerline tight in the field — it is at the code maximum.",
 body=("Value by value:\n\n"
  " 604.2   WC centerline 16-18 in. from side wall    drawing: 18\"           OK (at the max)\n"
  " 604.3.1 clearance 60 in. side x 56 in. rear       drawing: 60\" / 56\"     OK\n"
  " 604.4   seat height 17-19 in.                     drawing: 17\"-19\"       OK\n"
  " 604.5.1 side grab bar 42 in. min, 12 in. max from rear wall, 54 in. min from rear wall\n"
  "                                                    drawing: 42\"/12\"/54\"  OK\n"
  " 604.5.2 rear grab bar 36 in. min, 12 in. / 24 in. legs\n"
  "                                                    drawing: 36\"/12\"/24\"  OK\n"
  " 609.4   grab bar height 33-36 in. AFF             drawing: 33\"-36\"       OK\n"
  " 604.7   paper dispenser 7-9 in. in front of bowl, 15 in. min / 48 in. max AFF\n"
  "                                                    drawing: 7\"-9\", 15\" MIN, 48\" MAX  OK\n\n"
  "ONE NOTE: 604.2 allows 16 to 18 inches. The detail is drawn at 18 — the maximum. It is compliant as drawn, "
  "but there is no tolerance left, so the field dimension has to hold.")),

dict(fid="V-10", pg=2, anchor="ACCESSIBLE LAVATORY REQUIREMENTS", hit=0, disc="Accessibility",
 status="PASS", sev="VERIFIED", title="Lavatory rim, knee and toe clearances and mirror height are correct",
 checked="The lavatory and vanity details against FBC-Accessibility 306.2, 306.3, 603.3 and 606.3.",
 result="Rim or counter surface 34 in. max AFF (606.3); knee clearance 27 in. high at the front edge with 8 in. min depth (306.3); toe clearance 9 in. high, 17-25 in. deep (306.2); mirror bottom edge 40 in. max AFF (603.3). All match.",
 code="FBC-A 306.2 · 306.3 · 603.3 · 606.3", action="None.",
 body=(" 606.3 lavatory rim 34 in. max AFF          drawing: 34\" MAX          OK\n"
  " 306.3 knee clearance 27 in. high, 8 in. deep min at 27 in.\n"
  "                                            drawing: 27\" MIN, 8\" MIN  OK\n"
  " 306.2 toe clearance 9 in. high, 17-25 in. deep\n"
  "                                            drawing: 9\" MIN, 17\"-25\"  OK\n"
  " 603.3 mirror bottom edge 40 in. max AFF    drawing: 40\" MAX A.F.F.   OK\n\n"
  "The vanity / single-occupancy variant carries the same clearances. Nothing to correct.")),

dict(fid="V-11", pg=2, anchor="ACCESSIBLE DRINKING FOUNTAIN REQUIREMENTS", hit=0, disc="Accessibility",
 status="PASS", sev="VERIFIED", title="Drinking fountain spout height, spout location and clear floor space are correct",
 checked="The drinking fountain detail against FBC-Accessibility 602.4, 602.5 and 602.7, cross-checked against the hi-lo dual-station unit scheduled on A-5.",
 result="Spout outlet 36 in. max AFF (602.4); spout 5 in. max from the front edge and located within the 15 in. reach shown (602.5); knee and toe clearance 27 in. / 9 in. and a 30 x 48 in. clear floor space (602.2). The A-5 equipment schedule specifies a hi-lo dual-station accessible fountain, which satisfies the requirement for both standing and wheelchair users.",
 code="FBC-A 602.2 · 602.4 · 602.5 · 602.7", action="None.",
 body=(" 602.4 spout outlet 36 in. max AFF          drawing: 36\" MAX     OK\n"
  " 602.5 spout 5 in. max from front edge      drawing: 5\" MAX      OK\n"
  " 602.2 clear floor space 30 x 48 in.        drawing: 30\"/48\"     OK\n"
  " knee 27 in. / toe 9 in.                    drawing: 27\"/9\" MIN  OK\n\n"
  "CROSS-SHEET: A-5 equipment mark 5 is a 'HI-LO DUAL-STATION ACCESSIBLE DRINKING FOUNTAIN WITH INTEGRATED BOTTLE "
  "FILLER'. A hi-lo unit serves both the wheelchair and standing requirement of 602.7, so the two sheets agree.")),

# ---- G-3 ----
dict(fid="V-12", pg=3, anchor="ACCESSIBLE DOOR CLEARANCES", hit=0, disc="Accessibility",
 status="PASS", sev="VERIFIED", title="Every maneuvering clearance matches Table 404.2.4.1 exactly",
 checked="All six door approach conditions on this sheet compared cell by cell against FBC-Accessibility Table 404.2.4.1.",
 result="Front pull 60 in. with 18 in. latch side; front push 48 in. with 0 in.; hinge pull 60 in. / 36 in. and 54 in. / 42 in.; hinge push 42 in. / 22 in.; latch pull 48 in. / 24 in.; latch push 42 in. / 24 in.; and the '+12 in. where the door has both a closer and a latch' qualifier. Every value matches the table. This is the most reliably drawn detail in the set.",
 code="FBC-A Table 404.2.4.1", action="None.",
 body=("Compared cell by cell against FBC-Accessibility Table 404.2.4.1:\n\n"
  " Front approach, pull side     60\" perpendicular, 18\" latch side    drawing: 60\" / 18\"   OK\n"
  " Front approach, push side     48\" perpendicular, 0\" latch side     drawing: 48\" / X     OK\n"
  " Hinge approach, pull side     60\" with 36\" parallel                drawing: X=36\" if Y=60\"  OK\n"
  " Hinge approach, pull side     54\" with 42\" parallel                drawing: X=42\" if Y=54\"  OK\n"
  " Hinge approach, push side     42\" with 22\" parallel                drawing: 22\" / Y=42\" MIN OK\n"
  " Latch approach, pull side     48\" with 24\" parallel                drawing: 24\" / Y=48\"     OK\n"
  " Latch approach, push side     42\" with 24\" parallel                drawing: 24\" / Y=42\"     OK\n"
  " Closer + latch adjustment     add 12 in.                           drawing: 'X=12\" IF DOOR HAS BOTH A LATCH AND A CLOSER'  OK\n\n"
  "Also on this detail: 32 in. min clear opening and 18 in. min latch-side clearance are both drawn correctly. "
  "Note the detail's own '36\" MIN DOOR' callout — which is what makes the 2'-8\" leaf at Door 104 an internal "
  "contradiction. See H-03.")),

dict(fid="V-13", pg=3, anchor="SIGNAGE REQUIREMENTS", hit=0, disc="Accessibility",
 status="PASS", sev="VERIFIED", title="Tactile signage character height, mounting and Braille placement are correct",
 checked="The signage notes and tactile sign detail against FBC-Accessibility 703.2, 703.3, 703.4 and 216.",
 result="Character height 5/8 in. to 2 in., uppercase sans serif, raised 1/32 in. min (703.2.4/703.2.5/703.2.6); width-to-height 3:5 to 1:1 and stroke 1:5 to 1:10 (703.2.4); Braille below the text with 3/8 in. min separation (703.3.2); mounting 48 in. min to 60 in. max AFF, latch side of the door, 18 in. x 18 in. clear approach outside the door swing (703.4.1/703.4.2); non-glare finish and high contrast (703.5.1). All correct.",
 code="FBC-A 216 · 703.2 · 703.3.2 · 703.4.1 · 703.4.2 · 703.5", action="None.",
 body=(" 703.2.5 character height 5/8 in. to 2 in.     drawing: 5/8\"-2\"        OK\n"
  " 703.2.6 raised 1/32 in. min                   drawing: 1/32\" MIN      OK\n"
  " 703.2.4 W:H 3:5 to 1:1, stroke 1:5 to 1:10    drawing: same           OK\n"
  " 703.3.2 Braille below text, 3/8 in. min sep.  drawing: 3/8\" MIN       OK\n"
  " 703.4.1 48 in. min / 60 in. max AFF           drawing: 48\" MIN / 60\" MAX  OK\n"
  " 703.4.2 latch side, 18 x 18 in. clear outside the door swing\n"
  "                                                drawing: 18\" MIN / 18\" MIN  OK\n"
  " 703.5.1 non-glare, high contrast              drawing: same           OK\n\n"
  "MINOR: 703.4.1 measures the 48 in. minimum to the baseline of the lowest TACTILE CHARACTER; the note measures "
  "it to the baseline of the lowest line of Braille, which sits below. That is the more conservative reading and "
  "produces a compliant sign either way — not worth a correction.")),

dict(fid="V-14", pg=3, anchor="CHANGE IN LEVEL REQUIREMENTS", hit=0, disc="Accessibility",
 status="PASS", sev="VERIFIED", title="Changes in level and threshold bevel are correct",
 checked="The three change-in-level details against FBC-Accessibility 303.2, 303.3 and 404.2.5.",
 result="Vertical change up to 1/4 in. permitted without treatment (303.2); 1/4 in. to 1/2 in. beveled at 1:2 (303.3); the combination detail applies both correctly. A-4 general note 7 separately requires ADA threshold heights and beveling to be verified at all finish transitions, which is the right instruction given the epoxy-to-tile change at the restroom doors.",
 code="FBC-A 303.2 · 303.3 · 404.2.5", action="None.",
 body=(" 303.2 vertical change <= 1/4 in., no treatment    drawing: 1/4\" MAX      OK\n"
  " 303.3 change 1/4 in. to 1/2 in., beveled 1:2      drawing: 1/2\" MAX, 1:2 OK\n"
  " combination detail applies both                    drawing: same           OK\n\n"
  "RELEVANT HERE: A-4 schedules FF-1 epoxy flooring in Reception, Mat Studio, Hall and Closet, and T-1 porcelain "
  "tile in both restrooms — so there is a real material transition at each restroom door. A-4 general note 7 "
  "already directs that these be verified against the ADA threshold and bevel limits. Correctly anticipated.")),

# ---- A-1 ----
dict(fid="V-15", pg=4, anchor="EXISTING TENANT SEPARATION", hit=0, disc="Fire and life safety",
 status="PASS", sev="VERIFIED", title="Demolition protects the rated tenant separation and maintains egress during construction",
 checked="The A-1 demolition notes and legend for instructions covering the two 1-hour tenant separation barriers and for continuity of exits, accessible routes and fire protection during construction.",
 result="Note 7 requires the one-hour tenant-separation barriers designated to remain to be protected, damage repaired, and new penetrations sealed with approved systems that restore the rating. Note 8 requires required exits, accessible routes, fire-protection systems and emergency access to be maintained throughout construction. Both barriers are identified on the plan as UL U465 1-hour fire barriers. This is the correct instruction set for a Level II alteration in an occupied multi-tenant shell.",
 code="FBC-EBC 803 · FBC-B 3303 · 3311", action="None.",
 body=("NOTE 7: 'PROTECT EXISTING ONE-HOUR TENANT-SEPARATION BARRIERS DESIGNATED TO REMAIN. REPAIR DEMOLITION "
  "DAMAGE AND SEAL NEW PENETRATIONS WITH APPROVED SYSTEMS THAT RESTORE THE REQUIRED RATING.'\n\n"
  "NOTE 8: 'MAINTAIN REQUIRED EXITS, ACCESSIBLE ROUTES, FIRE-PROTECTION SYSTEMS, AND EMERGENCY ACCESS THROUGHOUT "
  "CONSTRUCTION.'\n\n"
  "Both walls flanking the tenant space are labelled 'EXISTING TENANT SEPARATION (1 HR RATED FIRE BARRIER - "
  "U465)'. The existing storefront door and the existing exterior door are both marked to remain, so the two "
  "remote exits the egress analysis depends on survive the demolition scope. Nothing to correct.")),

# ---- A-2 ----
dict(fid="V-16", pg=5, anchor="PARTITION SCHEDULE", hit=0, disc="Fire-resistance",
 status="PASS", sev="VERIFIED", title="W1 tenant separation matches the UL U465 listing reproduced on A-11",
 checked="Each parameter of partition type W1 against the UL U465 design text reproduced in full on sheet A-11.",
 result="W1 is scheduled as UL U465, 1 hour, 6 in. metal studs at 16 in. o.c., slab to roof deck, one layer of 5/8 in. gypsum board each side. U465 requires studs min 3-5/8 in. deep at 24 in. o.c. max, runners at 24 in. o.c. max fastening, and 5/8 in. gypsum board one layer each side. Every scheduled value is at or inside the listing: 6 in. studs exceed the 3-5/8 in. minimum, 16 in. o.c. is tighter than the 24 in. maximum, and the A-10 detail's 18 and 20 gauge studs exceed the 25 MSG minimum.",
 code="UL U465 · FBC-B 707 · Table 707.3.10", action="None.",
 body=("W1 SCHEDULED: 'TENANT SEPARATION UL U465 - 1 HR. 6\" METAL STUDS @ 16\" O.C. FULL HEIGHT (SLAB TO ROOF DECK) "
  "WITH ONE LAYER OF 5/8\" GYPSUM BOARD ON EACH SIDE.'\n\n"
  "UL U465, as reproduced on A-11:\n"
  "  Item 1  runners 3-5/8 in. deep min, fasteners 24 in. OC max     detail: pins @ 24\" and 6\" from ends  OK\n"
  "  Item 2  studs 3-5/8 in. deep min, spaced 24 in. OC max          schedule: 6\" @ 16\" OC              OK\n"
  "  Item 2  studs min No. 25 MSG galv steel                         detail: 18 GA / 20 GA              OK\n"
  "  Item 4  gypsum board 5/8 in. thick, one layer each side         schedule: 5/8\" each side           OK\n\n"
  "The A-10 section for W1 additionally calls out 5/8 in. TYPE X board, a slotted deflection track with a min "
  "1/2 in. gap, and 3\" x 3\" 14 GA clip angles to the deck — all consistent with the listing.\n\n"
  "ONE THING TO CARRY TO THE FIELD: U465 Item 4 sets screw spacing at 8 in. o.c. along board edges and 12 in. "
  "o.c. in the field, with joints oriented vertically and staggered side to side. That spacing is not repeated on "
  "A-10, but A-2 general note 2 requires all rated assemblies to be built in strict compliance with the specified "
  "UL listing, which carries it. Worth a word at the pre-construction meeting rather than a plan correction.")),

# ---- A-3 ----
dict(fid="V-17", pg=6, anchor="REFLECTED CEILING PLAN", hit=0, disc="Means of egress / General",
 status="PASS", sev="VERIFIED", title="Ceiling heights clear the minimum by a wide margin; emergency and exit lighting are called for",
 checked="The ceiling heights on the reflected ceiling plan against the 7 ft 6 in. minimum in FBC-B 1003.2, and the sheet notes for emergency illumination and exit signage.",
 result="Finished ceilings are at 10'-0\" AFF in the restrooms, hall and closet, with the Reception and Mat Studio ceilings left open to structure at bar joists 14'-8\" to 15'-8\". The minimum is 7'-6\". Note 24 requires emergency lighting and illuminated exit signs at the locations shown, connected to emergency power and testing controls, and the E-2 lighting schedule carries both a type G emergency fixture and a type H emergency/exit combo.",
 code="FBC-B 1003.2 · 1008.3 · 1013", action="None.",
 body=("CEILING HEIGHTS: A.F.F. +10'-0\" at the enclosed rooms; main area ceiling to remain open to structure, "
  "bottom of bar joist +14'-8\" to +15'-8\".\n\n"
  "CODE: FBC-B 1003.2 — means of egress shall have a ceiling height of not less than 7 feet 6 inches. Clear by "
  "2'-6\" at the tightest point.\n\n"
  "EMERGENCY ILLUMINATION: note 24 — 'PROVIDE EMERGENCY LIGHTING AND ILLUMINATED EXIT SIGNS AT LOCATIONS SHOWN. "
  "CONNECT TO REQUIRED EMERGENCY POWER AND TESTING CONTROLS.' The E-2 legend carries type G (emergency lighting) "
  "and type H (emergency/exit combo), and A-8 shows the combo units in the Mat Studio elevations. The three "
  "sheets agree.\n\n"
  "NOT REVIEWED HERE: the structural capacity of the bar joists for the suspended infrared panels and light "
  "fixtures. Notes 19 through 23 give the right instructions — independent load-rated support, no support from "
  "grid, ductwork, conduit or sprinkler piping, no cutting or welding of joists without written structural "
  "approval — but the capacity itself is an engineering question outside this pass.")),

# ---- A-4 ----
dict(fid="V-18", pg=7, anchor="ROOM FINISH SCHEDULE", hit=0, disc="Interior finishes",
 status="PASS", sev="VERIFIED", title="Finish schedule is internally consistent and agrees with the ceiling plan",
 checked="Each room's ceiling height and ceiling type in the A-4 finish schedule against the heights shown on the A-3 reflected ceiling plan, and the wet-area finishes against the A-2 partition notes.",
 result="Reception 18'-0\" and Mat Studio 17'-8\" with EX (exposed structure to remain), Hall, Closet and both restrooms at 10'-0\" with C-1 suspended acoustical tile — matching the A.F.F. +10'-0\" and bar joist heights on A-3. Both restrooms carry T-1 porcelain floor tile and TL-1 wall tile to 48 in. AFF, consistent with A-2 general note 3 requiring cement backer board or water-resistant gypsum board at wet areas.",
 code="FBC-B 1210.2 (wall and floor finish materials in toilet rooms)", action="None.",
 body=("SCHEDULE vs A-3:\n"
  "  101 Reception     18'-0\"  EX   exposed ceiling to remain   A-3: open to structure     OK\n"
  "  102 Mat Studio    17'-8\"  EX   exposed ceiling to remain   A-3: open to structure     OK\n"
  "  103 Hall          10'-0\"  C-1                              A-3: A.F.F. +10'-0\"        OK\n"
  "  104 Closet        10'-0\"  C-1                              A-3: A.F.F. +10'-0\"        OK\n"
  "  105 Unisex ADA    10'-0\"  C-1                              A-3: A.F.F. +10'-0\"        OK\n"
  "  106 Unisex        10'-0\"  C-1                              A-3: A.F.F. +10'-0\"        OK\n\n"
  "WET AREAS: T-1 porcelain floor tile with a matte slip-resistant finish and TL-1 subway wall tile to 48 in. AFF "
  "in both restrooms, over the cement backer board or water-resistant gypsum board required by A-2 note 3. "
  "Consistent throughout.\n\n"
  "WHAT IS MISSING FROM THIS SHEET is the flame-spread classification — see M-06.")),

# ---- A-5 ----
dict(fid="V-19", pg=8, anchor="EQUIPMENT SCHEDULE", hit=0, disc="Means of egress / Accessibility",
 status="PASS", sev="VERIFIED", title="Equipment notes protect egress paths and accessible clearances; extinguishers and fountain are correct",
 checked="The A-5 general notes for protection of egress paths and accessible clear floor space, and the scheduled fire extinguishers and drinking fountain against NFPA 10 and FBC-Accessibility.",
 result="Note 6 requires accessible-use equipment to be coordinated with clear floor space, reach ranges and approach and prohibits movable equipment inside required accessible clearances. Note 7 prohibits any equipment encroaching into egress paths, exit corridors or door swing clearances. Mark 3 is a 10 lb ABC extinguisher wall-mounted per NFPA 10; mark 5 is a hi-lo dual-station accessible fountain with bottle filler; mark 13 is identified as an accessible reception counter — though it is not dimensioned as one, see M-07.",
 code="NFPA 10 · FBC-A 305 · 308 · 904.4", action="None on this sheet — see M-07 for the counter.",
 body=("NOTE 6: 'COORDINATE EQUIPMENT INTENDED FOR ACCESSIBLE USE WITH REQUIRED CLEAR FLOOR SPACE, REACH RANGES, "
  "AND APPROACH. DO NOT PLACE MOVABLE EQUIPMENT WITHIN REQUIRED ACCESSIBLE CLEARANCES.'\n\n"
  "NOTE 7: 'ENSURE NO EQUIPMENT ENCROACHES INTO REQUIRED EGRESS PATHS, EXIT CORRIDORS, OR DOOR SWING "
  "CLEARANCES.' — the right instruction for a room full of loose mats and benches.\n\n"
  "SCHEDULED ITEMS CHECKED:\n"
  " Mark 3   10-lb ABC fire extinguisher, wall mounted per NFPA 10        OK\n"
  " Mark 5   hi-lo dual-station accessible drinking fountain               OK, matches G-2 and G-1\n"
  " Mark 8   96 in. wall mirrors, bottom at 14 in. AFF                     OK, below the 40 in. limit\n"
  " Mark 13  'CUSTOM BUILT-IN ACCESSIBLE RECEPTION COUNTER'                identified but not dimensioned — M-07\n\n"
  "The 12 in. deep floating shelves at marks 9 and 10 sit within the 4 in. protrusion limit of FBC-A 307.2 only "
  "if mounted below 27 in. or above 80 in. AFF. A-7 shows them at 5'-3\" — confirm they are outside any "
  "circulation path, which the reception layout suggests they are.")),

# ---- A-6 ----
dict(fid="V-20", pg=9, anchor="ENLARGED FLOOR PLAN - RESTROOMS", hit=0, disc="Accessibility",
 status="PASS", sev="VERIFIED", title="Accessible restroom 105 — turning space, water closet clearance and clear floor spaces all check out",
 checked="The dimensioned enlarged plan of Restroom 105 against FBC-Accessibility 304.3.1, 305.3, 603.2.1 and 604.3.1, and the fixture marks against the accessible fixture schedule on the same sheet.",
 result="A 60 in. turning space, a 56 in. rear and 60 in. side water closet clearance, and 30 x 48 in. clear floor spaces at the lavatory and accessories are all dimensioned on the plan. Fixtures are marked 1A accessible water closet and 2A accessible lavatory, with a 42 in. and a 36 in. grab bar scheduled. This is the accessible half of the two-room cluster; Restroom 106 carries the standard 1B and 2B fixtures, which FBC-A 213.2 Exception 4 permits.",
 code="FBC-A 213.2 Exc. 4 · 304.3.1 · 305.3 · 603.2.1 · 604.3.1", action="None — but see H-03 regarding the door to Restroom 106.",
 body=("DIMENSIONED ON THE ENLARGED PLAN: 60\" turning space; 56\" and 60\" water closet clearances; 30\" x 48\" "
  "clear floor spaces; 3'-0\" door.\n\n"
  " 304.3.1 turning space 60 in. diameter        plan: 60\"        OK\n"
  " 604.3.1 WC clearance 60 in. side, 56 in. rear plan: 60\" / 56\"  OK\n"
  " 305.3   clear floor space 30 x 48 in.        plan: 30\" / 48\"  OK\n"
  " 603.2.1 turning space within the room        plan: shown      OK\n\n"
  "FIXTURES: 1A floor-mounted accessible water closet; 2A wall-mounted accessible lavatory; mark 6 a 42 in. "
  "1-1/2 in. dia. grab bar; mark 5 a 36 in. grab bar; mark 7 a wall-mounted paper dispenser. The 42 in. bar goes "
  "on the side wall and the 36 in. bar on the rear wall, per 604.5.1 and 604.5.2 — confirm the elevations place "
  "them that way round.\n\n"
  "TWO-ROOM CLUSTER: FBC-A 213.2 Exception 4 requires at least 50 percent, but not fewer than one room for each "
  "use, to be accessible where single-user toilet rooms are clustered. One of two is accessible. Satisfied.")),

dict(fid="V-21", pg=9, anchor="INTERIOR ELEVATION - ADA RESTROOM", hit=0, disc="Accessibility",
 status="PASS", sev="VERIFIED", title="Restroom accessory mounting heights are governed by the correct rule",
 checked="The four ADA restroom interior elevations and the A-6 general notes against FBC-Accessibility 308 reach ranges and 604/609 mounting heights.",
 result="General note 4 requires the highest operable or dispensing part of every accessory to be 48 in. max AFF for forward or side reach; note 5 requires 30 x 48 in. clear floor spaces at all fixtures and accessories; note 6 requires a 60 in. diameter or T-shaped turning space; note 3 requires operable parts usable with one hand without tight grasping, pinching or twisting. Note 7's 10-second metering faucet and note 8's 36 in. spout height also match code. The elevations dimension 4'-0\" tile, 10'-0\" ceilings and the 3'-0\" and 3'-6\" accessory bands consistent with those limits.",
 code="FBC-A 308.2.1 · 308.3.1 · 309.4 · 602.4 · 606.4", action="None.",
 body=("The controlling instructions are all on this sheet and all correct:\n\n"
  " Note 3  operable parts usable with one hand, no tight grasping/pinching/twisting   FBC-A 309.4   OK\n"
  " Note 4  highest operable part 48 in. max AFF, forward or side reach                FBC-A 308     OK\n"
  " Note 5  30 x 48 in. clear floor space at all fixtures and accessories              FBC-A 305.3   OK\n"
  " Note 6  60 in. diameter or T-shaped turning space                                  FBC-A 304.3   OK\n"
  " Note 7  metering faucet remains open 10 seconds minimum                            FBC-A 606.4   OK\n"
  " Note 8  drinking fountain spout 36 in. max AFF                                     FBC-A 602.4   OK\n\n"
  "Note 2 also requires solid wood blocking in the wall cavities for all wall-mounted fixtures, grab bars and "
  "accessories — which is what makes the 250 lb point load requirement of 609.8 achievable. Correctly "
  "anticipated at design stage rather than left to the field.")),

# ---- A-7 ----
dict(fid="V-22", pg=10, anchor="INTERIOR ELEVATION - RECEPTION", hit=0, disc="Accessibility / Coordination",
 status="PASS", sev="VERIFIED", title="Reception elevations coordinate with the ceiling plan and keep protrusions out of the reach band",
 checked="The reception elevations against FBC-Accessibility 307.2 protruding objects, and the suspended fixture heights against the A-3 reflected ceiling plan and its structural support notes.",
 result="Wall-mounted shelves are dimensioned at 5'-3\" and the merchandise and apparel shelves are 12 in. deep — above the 27 in. to 80 in. protrusion band only if they sit outside a circulation path, which the reception layout supports. Suspended light fixtures are called at 11'-0\" AFF on 1-5/8 in. unistrut clamped to the bar joist top chord, matching the A-3 support notes and well clear of the 80 in. headroom minimum in FBC-A 307.4.",
 code="FBC-A 307.2 · 307.4 · FBC-B 1003.3", action="None.",
 body=("Checked on this sheet:\n\n"
  " Suspended fixtures at 11'-0\" AFF on unistrut clamped to the joist top chord — clear of the 80 in. minimum "
  "headroom in FBC-A 307.4 and FBC-B 1003.3 by a wide margin.\n"
  " 12 in. deep floating shelves at 5'-3\" — a 12 in. projection is far more than the 4 in. that FBC-A 307.2 "
  "allows within the 27 in. to 80 in. band. 5'-3\" is 63 in., which is inside that band. This is compliant only "
  "because the shelves sit against the reception back wall rather than along a circulation path. Worth one look "
  "in the field to confirm nothing routes traffic under them.\n"
  " Camera and light locations are called by reference to the RCP rather than dimensioned here, and the two "
  "sheets agree.\n\n"
  "The reception counter itself is detailed on A-9 and is the subject of M-07.")),

# ---- A-8 ----
dict(fid="V-23", pg=11, anchor="INTERIOR ELEVATION - MAT STUDIO", hit=0, disc="Fire and life safety",
 status="PASS", sev="VERIFIED", title="Exit and emergency lighting appear in the studio elevations, consistent with the RCP and E-2",
 checked="The four Mat Studio elevations for exit and emergency lighting, and the suspended infrared panel and speaker heights against the A-3 support notes.",
 result="Three 'EXIT/ EMERGENCY COMBO LIGHT PER RCP PLAN' callouts appear across elevations 2 and 3, which is consistent with the type H fixture in the E-2 lighting schedule and with A-3 note 24. Suspended infrared heating panels are called at 10'-6\" AFF on hanger rod to unistrut clamped to the bar joist bottom chord, and pendant speakers at 12'-0\" — both above the 80 in. headroom minimum and both routed to the independent support required by A-3 notes 19 through 23.",
 code="FBC-B 1008.3 · 1013 · FBC-A 307.4", action="None.",
 body=("EXIT AND EMERGENCY LIGHTING: three 'EXIT/ EMERGENCY COMBO LIGHT PER RCP PLAN' callouts across the studio "
  "elevations. Cross-checks against E-2 type H 'EMERGENCY/ EXIT LIGHT COMBO' and A-3 note 24. Three sheets in "
  "agreement.\n\n"
  "SUSPENDED EQUIPMENT HEIGHTS:\n"
  "  infrared heating panels   10'-6\" AFF   hanger rod to unistrut, clamped to joist bottom chord\n"
  "  pendant speakers          12'-0\" AFF\n"
  "  surface-mounted illuminated signage, LED rope light, cameras — all per RCP\n\n"
  "All are clear of the 80 in. minimum headroom. The panels sit 6 in. above the 10'-0\" ceiling datum used "
  "elsewhere, in a space that is open to structure — no conflict.\n\n"
  "NOT REVIEWED: the structural adequacy of the W24x76 beam, the bar joists and the unistrut for the suspended "
  "load. A-3 gives the correct instructions; the capacity itself is an engineering question.")),

# ---- A-10 ----
dict(fid="V-24", pg=13, anchor="EXISTING TENANT SEPARATION", hit=0, disc="Fire-resistance",
 status="PASS", sev="VERIFIED", title="Partition sections are consistent with U465 and with the A-2 partition schedule",
 checked="Each partition section on A-10 against the corresponding W-type in the A-2 partition schedule and, for the rated type, against the UL U465 text on A-11.",
 result="The rated section shows 5/8 in. Type X gypsum board each side over 6 in. 20 gauge studs at 16 in. o.c., with a slotted deflection track fastened with two #10 screws at 16 in. o.c., a min 1/2 in. friction-fit gap at the deflection track, and 3 x 3 in. 14 gauge clip angles to the metal roof deck. The non-rated sections use 3-5/8 in. 20 gauge studs at 16 in. o.c. and call for cement board or mold-resistant gypsum board at wet areas and tile locations. All consistent with the schedule and the listing.",
 code="UL U465 · FBC-B 707.3.10 · 715 (joint systems)", action="None.",
 body=("RATED SECTION vs the listing and the schedule:\n"
  "  5/8\" TYPE X gypsum board each side              U465 Item 4: 5/8 in.               OK\n"
  "  6\" steel studs 20 GA @ 16\" O.C.                 U465 Item 2: 3-5/8 in. min, 24 in. OC max, 25 MSG min  OK\n"
  "  Bottom track, 1/4\" powder-actuated pins @ 24\" and 6\" from ends    U465 Item 1: 24 in. OC max   OK\n"
  "  Top track #12 screws @ 16\" O.C.                  tighter than the 24 in. maximum    OK\n"
  "  Slotted deflection track, min 1/2\" friction-fit gap, 3x3 14 GA clip angles to deck\n\n"
  "The deflection detail is the right approach for a slab-to-deck barrier under a metal roof deck. The joint "
  "system at the head of the wall must itself be a tested assembly under FBC-B 715 — A-12 lists head-of-wall "
  "firestop designs HW-D-0256 and BW-S-0006, which is where that gets satisfied.\n\n"
  "WET AREAS: the non-rated sections correctly call for cement board or mold-resistant gypsum board at tile "
  "locations, matching A-2 note 3 and the T-1/TL-1 finishes on A-4.")),

# ---- A-11 ----
dict(fid="V-25", pg=14, anchor="Nonbearing Wall Rating", hit=0, disc="Fire-resistance",
 status="PASS", sev="VERIFIED", title="The UL U465 listing is reproduced in full and is the correct listing for this wall",
 checked="That the UL design reproduced on this sheet is the one referenced by partition type W1, that it carries the rating claimed, and that its construction items support the scheduled assembly.",
 result="Design No. U465, dated February 25, 2015, Nonbearing Wall Rating 1 HR. That is the design number and the rating the A-1, A-2 and A-10 sheets all reference for the tenant separation. The full item list is machine-readable in this file, which is what allowed W1 to be checked line by line rather than taken on trust.",
 code="UL U465 · FBC-B 703.2 · 707.3.10", action="None.",
 body=("SHEET CONTENT: 'Design No. U465 / February 25, 2015 / Nonbearing Wall Rating — 1 HR.'\n\n"
  "This is the correct listing to reproduce: A-1 labels both flanking walls 'EXISTING TENANT SEPARATION (1 HR "
  "RATED FIRE BARRIER - U465)', A-2 schedules W1 as 'TENANT SEPARATION UL U465 - 1 HR', and A-10 details it. "
  "Four sheets, one design number, no drift.\n\n"
  "A NOTE ON METHOD: this sheet is live text in the PDF, not a scanned image. That is why V-16 and V-24 could be "
  "checked against the listing's own Items 1, 2 and 4 instead of simply confirming that a UL number appears "
  "somewhere on the drawing. Sheet A-12 does not have that property — see the scope note there.")),

# ---- A-12 ----
dict(fid="V-26", pg=15, anchor="UL DESIGN: W-L-1054", hit=0, disc="Fire-resistance",
 status="PASS", sev="VERIFIED", title="Firestop systems are named for each penetration condition — but the details are a raster insert",
 checked="Which UL firestop systems are cited, and whether they cover the penetration conditions this project actually creates.",
 result="Six systems are named: W-L-1054 and W-L-2244 (through-penetrations of a rated wall), C-AJ-1421 (floor or wall, mixed construction), BW-S-0006 (bottom-of-wall joint) and HW-D-0256 (head-of-wall joint). Between them they cover the penetration and joint conditions created by routing new plumbing, ductwork and conduit through the U465 barriers and by terminating the new slab-to-deck partitions. Naming specific systems rather than a generic 'approved firestopping' note is the correct level of documentation.",
 code="FBC-B 714 (penetrations) · 715 (fire-resistant joint systems)", action="None — but see the scope note on this sheet.",
 body=("SYSTEMS CITED: W-L-1054, W-L-2244, C-AJ-1421, BW-S-0006, HW-D-0256.\n\n"
  "  W-L-xxxx   through-penetration firestop, Wall, Loose (pipe/conduit through a rated wall)\n"
  "  C-AJ-xxxx  through-penetration, Concrete or masonry, mixed construction\n"
  "  BW-S-xxxx  bottom-of-wall joint system\n"
  "  HW-D-xxxx  head-of-wall joint system, dynamic\n\n"
  "This set creates exactly those conditions: new sanitary and water piping crossing to the existing chase, "
  "exhaust ducts, branch conduit, and new slab-to-deck partitions abutting a metal roof deck. Citing specific "
  "listed systems by number satisfies FBC-B 714 and 715 in a way a generic note would not.\n\n"
  "SCOPE LIMIT ON THIS SHEET: the system details themselves are placed as raster images — 42 embedded images, "
  "about 206 megapixels, 68 words of live text. The design numbers above were read; the parameters inside each "
  "system (annular space, sealant depth, pipe size limits) were not machine-checked. Verify at submittal that "
  "each cited system's parameters match the actual penetrations.")),

# ---- M-2 ----
dict(fid="V-27", pg=17, anchor="TYP. DUCT HANGING DETAILS", hit=0, disc="Mechanical",
 status="PASS", sev="VERIFIED", title="Duct construction and support details follow SMACNA and match the A-3 support strategy",
 checked="The duct hanging, transition, elbow and wall-penetration details for consistency with SMACNA duct construction standards and with the independent-support requirements on A-3.",
 result="Hangers are shown as threaded rod to 2x2x3/16 angle or 1-5/8 in. unistrut clamped to existing structural beams and bar joist bottom chords, with size limits of 50 in. dia. max and 24 in. dia. max on the two hanger types. Transitions are limited to 20 degrees on diverging flow and 30 degrees converging, elbows are drawn at R = 3D/2 full radius or with turning vanes, and the sheet defers splitter vane and tie rod location to SMACNA. The duct-through-wall detail shows rigid fiberglass board insulation with a galvanized protection sheet.",
 code="FBC-M 603 · SMACNA HVAC Duct Construction Standards", action="None.",
 body=("CHECKED ON THIS SHEET:\n"
  "  Hanger types           threaded rod to 2x2x3/16 angle or 1-5/8 unistrut, C-clamped to existing beam or joist\n"
  "  Size limits            50 in. dia. max and 24 in. dia. max on the two hanger arrangements\n"
  "  Transitions            20 degrees max on diverging flow, 30 degrees converging\n"
  "  Elbows                 R = 3D/2 full radius, or short radius with double vanes at R1/R2\n"
  "  Splitter vanes         deferred to SMACNA standard for location and tie rods\n"
  "  Wall penetration       rigid fiberglass board insulation with galvanized protection sheet\n\n"
  "These are standard, correctly drawn details, and the support approach matches A-3 notes 19 through 23 — "
  "independent load-rated support off the structure, not off the ceiling grid.\n\n"
  "NOT REVIEWED: duct insulation R-values against FBC Energy Conservation, and duct leakage testing. Neither "
  "appears on this sheet.")),

# ---- E-1 ----
dict(fid="V-28", pg=18, anchor="ELECTRICAL POWER PLAN", hit=0, disc="Electrical",
 status="PASS", sev="VERIFIED", title="GFCI protection, the roof receptacle and circuit identification are all handled correctly",
 checked="Receptacle types and locations on the power plan against NEC 210.8(B) GFCI requirements and NEC 210.63 equipment service receptacle, and the general notes against NEC/NFPA 70 and FBC Energy Conservation.",
 result="GFI receptacles are shown at both restrooms and at the exterior and roof locations, satisfying NEC 210.8(B) for bathrooms, rooftops and outdoors in other than dwelling units. A dedicated weatherproof roof receptacle is shown adjacent to RTU-2, satisfying NEC 210.63's requirement for a service receptacle within 25 ft of heating, air conditioning and refrigeration equipment. Every device on the plan carries a panel-A circuit number, and note 2 correctly cites NEC/NFPA 70, the Florida Building Code — Energy Conservation, and local amendments.",
 code="NEC 210.8(B) · 210.63 · FBC-EC", action="None.",
 body=("CHECKED:\n"
  "  GFI at both restrooms                       NEC 210.8(B)(1) bathrooms                OK\n"
  "  GFI WP at exterior / roof                   NEC 210.8(B)(4) outdoors, (B)(8) rooftop OK\n"
  "  'DEDICATED ROOF RECEPTACLE ABOVE' at RTU-2  NEC 210.63 service receptacle within 25 ft  OK\n"
  "  Every device tagged with a panel A circuit number — A-1, A-3, A-7, A-9 through A-17, A-32 through A-34\n"
  "  Show-window receptacle 'INSTALLED WITHIN 18\" OF THE TOP OF THE WINDOW'   NEC 210.62      OK\n\n"
  "The show-window note is a nice catch by the designer — NEC 210.62 requires at least one receptacle within "
  "18 inches of the top of a show window for each 12 linear feet, and the schedule carries two show-window "
  "circuits (33 and 34).\n\n"
  "NOT REVIEWED: conductor sizing, voltage drop, and the Energy Conservation compliance path.")),

# ---- E-2 ----
dict(fid="V-29", pg=19, anchor="LIGHTING SCHEDULE", hit=0, disc="Electrical / Life safety",
 status="PASS", sev="VERIFIED", title="Emergency and exit fixtures are scheduled, and occupancy sensing is provided",
 checked="The lighting legend and schedule for emergency illumination and exit signage, and for the automatic lighting controls required by the energy code.",
 result="Type G emergency lighting (2 W) and type H emergency/exit light combo (5 W) are both scheduled and appear on the plan, matching A-3 note 24 and the A-8 elevations. Ceiling occupancy sensors and occupancy sensor switches both appear in the legend, which is the control type the energy code requires for a space of this size. The infrared heating panels are correctly separated onto 208 V two-pole circuits through six 30 A power control relays, with a note limiting each relay to three panels.",
 code="FBC-B 1008.3 · 1013 · FBC-EC C405 · NEC 700.12", action="None.",
 body=("EMERGENCY ILLUMINATION AND EXIT SIGNAGE:\n"
  "  Type G  EMERGENCY LIGHTING           2 W, 120 V\n"
  "  Type H  EMERGENCY/ EXIT LIGHT COMBO  5 W, 120 V\n"
  "Both appear in the legend, in the schedule, on the plan, in the A-8 elevations and in A-3 note 24. Five "
  "sheets, consistent.\n\n"
  "AUTOMATIC CONTROLS: the legend carries both 'OS CEILING OCCUPANCY SENSOR' and an occupancy sensor switch "
  "type, which is the control category FBC Energy Conservation C405 calls for in this occupancy.\n\n"
  "INFRARED PANEL CIRCUITING: 1500 W panels at 208 V, single phase, on two-pole 30 A circuits through '(6) 30A "
  "INFRARED POWER CONTROL RELAY' with 'MAX. (3) INFRARED PANELS PER POWER CONTROL RELAY'. Three 1500 W panels is "
  "4500 W, or 21.6 A at 208 V — 72 percent of a 30 A circuit, inside the 80 percent continuous-load limit of "
  "NEC 210.20(A). Correctly sized.\n\n"
  "NOT REVIEWED: lighting power density against the energy code, and emergency illumination levels at the floor.")),

# ---- E-3 ----
dict(fid="V-30", pg=20, anchor="PANEL SCHEDULE", hit=0, disc="Electrical",
 status="PASS", sev="VERIFIED", title="Connected load recomputed — the panel and the service both have substantial headroom",
 checked="Every per-phase kVA entry in the Panel A schedule summed and converted to amperes, then compared against the 250 A panel, the 250 A fused disconnect and the 1000 A existing service.",
 result="The per-phase column totals 62.36 kVA, which at 208 V three-phase is 173 A. Panel A is a 250 A MLO panel fed through a 250 A fused disconnect off an existing 1000 amp wireway — so the connected load is 69 percent of the panel and 17 percent of the service. Feeder is 4-350 KCM + 1 #2 G aluminium in 4 in. conduit. The 65 kAIC rating shown at the main service switch is the value the new panel's interrupting rating has to coordinate with.",
 code="NEC 220 · 408.36 · 110.9", action="Confirm Panel A's series or fully-rated interrupting rating against the 65 kAIC available fault current shown.",
 body=("RECOMPUTED FROM THE SCHEDULE'S OWN PER-PHASE COLUMN:\n"
  "  0.72 + 9.38 + 6.11 + 7.08 + 1.57 + 4.00 + 3.86 + 3.33 + 2.79 + 4.50 + 4.50 + 3.75 + 3.75 + 2.00 + 1.65 + "
  "0.57 + 2.80 = 62.36 kVA\n"
  "  62,360 VA / (208 V x 1.732) = 173 A\n\n"
  "  Panel A          250 A MLO, 120/208 V 3-phase 4-wire     load at 69 percent\n"
  "  Fused disconnect 250 A                                    same\n"
  "  Existing service 1000 A, 120/208 V 3-phase 4-wire, 65 kAIC   load at 17 percent\n"
  "  Feeder           4-350 KCM + 1 #2 G aluminium in 4\" conduit; 250 KCM G aluminium\n\n"
  "Ample headroom — the eighteen infrared heating panels are the dominant load and they still leave the panel at "
  "roughly two-thirds.\n\n"
  "ONE THING TO CONFIRM: the riser shows 65 kAIC at the main service breaker. Under NEC 110.9 the new equipment "
  "has to have an interrupting rating at least equal to the available fault current at its terminals. That will "
  "be lower than 65 kA after the feeder impedance, but the panel's rating — series or fully rated — should be "
  "stated on the schedule.")),

dict(fid="V-31", pg=20, anchor="MAT STUDIO & EMERGENCY LIGHTING", hit=0, disc="Electrical / Life safety",
 status="PASS", sev="VERIFIED", title="Emergency lighting shares the branch circuit with the normal lighting it serves — which is what the NEC requires",
 checked="How the emergency lighting is fed, against NEC 700.12 unit equipment requirements.",
 result="Circuit 14 is 'MAT STUDIO & EMERGENCY LIGHTING' — the emergency fixtures are on the same branch circuit that serves the normal lighting in the same area. For battery-equipped unit equipment that is not a shortcut, it is the required arrangement: it guarantees the units sense loss of normal power in the space they illuminate. Confirm the connection is made ahead of any local switch.",
 code="NEC 700.12 (unit equipment) · FBC-B 1008.3", action="Confirm the emergency units are connected ahead of any local switching, and that the 90-minute duration of 1008.3 is met.",
 body=("SCHEDULE ROW: circuit 14, 'MAT STUDIO & EMERGENCY LIGHTING', 20 A, 1 pole, load type L, 0.36 kVA.\n\n"
  "This reads at first glance like emergency lighting improperly sharing a general circuit. It is the opposite. "
  "NEC 700.12 requires battery-equipped unit equipment to be connected to the same branch circuit that serves "
  "the normal lighting in the area it illuminates, and connected ahead of any local switches — so that a loss of "
  "that circuit is what triggers the units.\n\n"
  "TWO THINGS TO CONFIRM AT SUBMITTAL:\n"
  " - The connection is ahead of the local switching, not downstream of it.\n"
  " - FBC-B 1008.3 requires the emergency illumination to last 90 minutes at the required levels. Verify the "
  "battery duration of the selected type G and type H fixtures.\n\n"
  "Circuit 5 similarly carries 'RESTROOM/HALL LIGHTING & EF' — same principle for any unit equipment in that "
  "area.")),

# ---- P-1 ----
dict(fid="V-32", pg=21, anchor="SANITARY PLAN", hit=0, disc="Plumbing",
 status="PASS", sev="VERIFIED", title="Fixture types match the G-1 count and the drain sizes carry the connected load",
 checked="The fixtures shown on the sanitary plan against the fixture count verified on G-1, and the drain and branch sizes against the drainage fixture unit values in FBC-P Table 709.1 and the capacities in Table 710.1(1).",
 result="Two water closets, two lavatories, one drinking fountain and one mop service sink are shown — exactly the fixture set the G-1 Table 2902.1 analysis requires. Connected drainage load is roughly 14 DFU. The 4 in. building drain and 4 in. vent through roof are shown, with 3 in. branches to the water closets and 2 in. to the lavatories and fountain. A 3 in. horizontal branch is limited to 2 water closets, and exactly 2 are connected.",
 code="FBC-P Table 709.1 · Table 710.1(1) · 710.1.1", action="None — spot check only, see the note below.",
 body=("FIXTURES SHOWN: WC x2, LAV x2, DF (hi/lo) x1, MS (mop service sink) x1, plus 2 floor drains and floor "
  "cleanouts. That is exactly the set the G-1 fixture analysis requires — see V-06.\n\n"
  "DRAINAGE LOAD, spot check against FBC-P Table 709.1:\n"
  "  2 water closets, public, flushometer or tank    ~4 DFU each   = 8\n"
  "  2 lavatories                                     1 DFU each   = 2\n"
  "  1 drinking fountain                              0.5 DFU      = 0.5\n"
  "  1 mop service sink                               ~3 DFU       = 3\n"
  "  approximate total                                             ~14 DFU\n\n"
  "PIPE SIZES SHOWN: 4 in. building drain, 4 in. VTR, 3 in. branches at the water closets, 2 in. at lavatories "
  "and the fountain. Table 710.1(1) allows far more than 14 DFU on a 4 in. drain, and limits a 3 in. horizontal "
  "branch to 2 water closets — exactly the number connected.\n\n"
  "SCOPE: this is a load and size spot check, not a full hydraulic review. Developed length, slope and the "
  "capacity of the existing building sewer were not verified — plumbing note 3 correctly directs the contractor "
  "to field-verify existing sizes, elevations and available capacity before construction.\n\n"
  "WHAT IS MISSING is trap seal protection at the two floor drains — see M-08.")),

# ---- P-2 ----
dict(fid="V-33", pg=22, anchor="WATER PLAN", hit=0, disc="Plumbing",
 status="PASS", sev="VERIFIED", title="Water distribution sizes step down consistently and the point-of-use heaters remove the long hot-water runs",
 checked="The cold water pipe sizes on the water plan for consistency from the 1 in. main to the 1/2 in. fixture branches, and the water heater arrangement against the fixtures served.",
 result="A 1 in. cold water main runs to the wet wall and steps to 3/4 in. distribution and 1/2 in. fixture branches, which is a conventional and adequate arrangement for a 14-DFU load. Three instantaneous water heaters are located at the point of use — WH-1 and WH-2 below the two lavatories, WH-3 adjacent to the mop sink — so there is no recirculation loop and no long hot-water run to insulate.",
 code="FBC-P 604 · FBC-EC C404", action="None.",
 body=("SIZES SHOWN: 1\" CW main, 3/4\" CW distribution, 1/2\" CW fixture branches.\n\n"
  "Conventional and adequate for the connected load. FBC-P 604 sizing depends on developed length and available "
  "pressure, neither of which is stated — plumbing note 7 directs that the sizing be done for the connected "
  "fixture load, developed length, available pressure and applicable code, which is the right instruction.\n\n"
  "WATER HEATERS: 'INSTA-HOT WATER HEATER BELOW LAVATORY' at each restroom and 'INSTA-HOT WATER ADJACENT TO MOP "
  "SINK'. Point-of-use instantaneous heaters at each fixture group eliminate the hot-water distribution run "
  "entirely — which sidesteps the pipe insulation and maximum-run requirements a central heater would trigger "
  "under the energy code. Each is 3500 W at 120 V per the E-2 equipment schedule, on its own 30 A circuit "
  "(circuits 3, 11 and 13).\n\n"
  "CONFIRM: hot water delivered to a public lavatory is limited to 110 degrees F by FBC-P 416.5. Specify the "
  "temperature setting or a limiting device on the selected heaters.")),

# ---- P-3 ----
dict(fid="V-34", pg=23, anchor="4\" VTR", hit=0, disc="Plumbing",
 status="PASS", sev="VERIFIED", title="Vent sizes and the vent through roof are correct for the connected load",
 checked="The vent sizes on the sanitary riser against FBC-P 906 and 916, and the vent through roof against 903.1.",
 result="A 4 in. vent through roof serves the system, with 3 in., 2 in. and 1-1/2 in. branch vents shown at the water closets, lavatories, fountain and mop sink. A branch vent must be at least half the diameter of the drain it serves and never less than 1-1/4 in.; every vent shown satisfies that. The riser also shows floor cleanouts at each branch and a stub cap, with the standard cleanout, floor drain and vent-through-roof details on the same sheet.",
 code="FBC-P 903.1 · 906.1 · 916", action="None.",
 body=("VENTS SHOWN ON THE RISER: 4\" VTR; 3\" V; 2\" V at the water closets and mop sink; 1-1/2\" V at the "
  "lavatories.\n\n"
  "  906.1  branch vent not less than half the diameter of the drain served, min 1-1/4 in.\n"
  "         3\" WC drain -> 2\" vent is 2/3 of the drain      OK\n"
  "         2\" lav drain -> 1-1/2\" vent is 3/4 of the drain  OK\n"
  "  903.1  vent extension through the roof, correctly sized and flashed\n"
  "         detail shows 12 in. above the roof deck with a correctly sized rubber boot for the TPO roof   OK\n\n"
  "CLEANOUTS: floor cleanouts are shown at each branch and at the end of the line, with the standard cleanout "
  "tee and 45-degree wye details on this sheet. Pipe support details show clevis hangers with galvanized shields "
  "and high-compression insulation at each hanger point, which is the correct treatment for insulated lines.\n\n"
  "SCOPE: sizes checked against the tables; developed length and slope were not verified.")),

dict(fid="H-01r", pg=5, anchor="MAT STUDIO", hit=0, disc="Occupancy",
 status="OPEN", sev="HIGH", title="Mat Studio 102 — the room the occupant-load question turns on",
 checked="The 994 SF Mat Studio against the occupant density assigned to it on every sheet that assigns one.",
 result="Classified three ways across the set: assembly at 15 SF net for egress (67 occupants), health club / aerobics at 40 persons per 1000 SF for ventilation (40 occupants), and — unused — Table 1004.5 'Exercise rooms' at 50 SF gross (20 occupants). Whichever governs has to be stated once and carried onto G-1, M-1 and P-1. See H-01.",
 code="FBC-B 1004.5 · Table 1004.5", action="Resolve on G-1 — see H-01.",
 body=("994 SF. See H-01 on G-1.\n\nThis room is classified three different ways across the set: 'ASSEMBLY' at 15 SF "
  "net for egress (67 occupants), health-club/aerobics at 40 persons per 1000 SF for ventilation (40 occupants), "
  "and — unused — FBC-B Table 1004.5 'Exercise rooms' at 50 SF gross (20 occupants).\n\n"
  "Whichever governs must be stated once and carried consistently onto G-1, M-1 and P-1.")),

dict(fid="V-35", pg=16, anchor="DETECTOR TO REMAIN", hit=0, disc="Mechanical",
 status="PASS", sev="VERIFIED", title="The ventilation arithmetic is correct, and duct smoke detection is provided",
 checked="Every line of the Outdoor Air Calculations block recomputed from its own inputs, the supply air volume against the duct smoke detector threshold, and the exhaust total against the outdoor air for building pressurization.",
 result="Reception 164 SF at 10 per 1000 gives 2 persons, Rp 5 x 2 plus Ra 0.06 x 164 = 20 CFM; Mat Studio 994 SF at 40 per 1000 gives 40 persons, Rp 20 x 40 plus Ra 0.06 x 994 = 860 CFM; closet 2 CFM. Total 882, sheet says 881 — rounding only. The requirement was computed correctly. Supply air of 3000 CFM exceeds the 2000 CFM threshold for duct smoke detection, and an existing SD detector to remain is shown. Exhaust of 210 CFM against 350 CFM supply outdoor air gives the +140 CFM net positive pressurization noted, which is correct for this occupancy.",
 code="FBC-M 403.3.1.1 · 403.3.1.1.1.1 · 606.2.1", action="Confirm the existing SD is listed for duct installation, downstream of the filters, and interfaced to the fire alarm.",
 body=("The problem on this sheet is the equipment selection, not the engineering. The calculation itself is right, "
  "line by line:\n\n"
  "  Reception   164 SF @ 10/1000 -> 2 persons;  Rp 5 x 2 = 10;    Ra 0.06 x 164 = 9.8  =>  20 CFM\n"
  "  Mat Studio  994 SF @ 40/1000 -> 40 persons; Rp 20 x 40 = 800; Ra 0.06 x 994 = 59.6 => 860 CFM\n"
  "  Closet                                       Ra 0.12 x 13                          =>   2 CFM\n"
  "  Recomputed total 882 CFM; the sheet states 881. Rounding.\n\n"
  "DUCT SMOKE DETECTION: supply air is 3000 CFM, above the 2000 CFM threshold in FBC-M 606.2.1. An existing "
  "'SD - DETECTOR TO REMAIN' is shown. Confirm it is listed for duct installation, located downstream of the "
  "filters, and interfaced to the fire alarm system.\n\n"
  "PRESSURIZATION: EF-1, EF-2 and EF-3 at 70 CFM each total 210 CFM exhaust against 350 CFM of supply outdoor "
  "air, giving the '+140' net positive building pressure noted on the Air Balance table. Intentional, correct for "
  "a studio with two toilet exhausts, and unaffected by C-01.\n\n"
  "It is worth being explicit about this: the designer did the ventilation calculation properly. See C-01 for "
  "what was then scheduled against it.")),

dict(fid="V-36", pg=12, anchor="MAINTAIN REQUIRED ADA CLEARANCES", hit=0, disc="Accessibility / Millwork",
 status="PASS", sev="VERIFIED", title="The counter detail carries the right instruction and the knee space is achievable",
 checked="The reception counter section for knee and toe clearance against FBC-Accessibility 306, and the millwork notes for who is responsible for maintaining the clearances.",
 result="The detail is noted 'MILLWORK FABRICATOR TO PROVIDE ADEQUATE SUPPORT FOR COUNTERTOP. MAINTAIN REQUIRED ADA CLEARANCES' and shows the marble top carried on 2x supports and a double layer of 3/4 in. plywood rather than on a front apron — so a 27 in. knee clearance is achievable within the section as drawn. What the detail does not do is identify or dimension the accessible portion of the counter, which is finding M-07.",
 code="FBC-A 306.2 · 306.3 · 904.4.1", action="None on the construction detail — see M-07 for the dimension.",
 body=("WHAT IS RIGHT ON THIS DETAIL:\n"
  " - The note 'MILLWORK FABRICATOR TO PROVIDE ADEQUATE SUPPORT FOR COUNTERTOP. MAINTAIN REQUIRED ADA CLEARANCES' "
  "appears on both counter sections, placing the obligation with the fabricator in writing.\n"
  " - The 3/4 in. marble top is carried on 2x supports and a double layer of 3/4 in. plywood, not on a continuous "
  "front apron. That construction leaves the underside free, so the 27 in. knee clearance and 9 in. toe clearance "
  "of FBC-A 306 can be achieved without redesigning the section.\n"
  " - Waterfall edges are mitred and wrapped, and all exposed surfaces are finish-grade plywood glued and screwed "
  "to the 2x supports.\n\n"
  "WHAT IS MISSING is not construction, it is designation: which segment of the counter is the accessible sales "
  "and service counter, and what height is it. See M-07.")),
]

# ── one-line rail-card summaries, keyed by finding id ─────────────────────
LINE = {
"C-01":"<b>881 CFM required</b> by this sheet's own table vs <b>350 CFM</b> scheduled on RTU-2. 531 CFM short. FBC-M 403.3.1.1 / 405.1.",
"C-01b":"RTU-2: Supply 3000 / Return 2650 / <b>Outside Air 350</b> / Exhaust 0. See C-01 on this sheet.",
"C-01c":"Air Balance repeats the same <b>350 CFM</b> outside air. Building pressure +140 is intentional and unaffected.",
"H-01":"1/15 egress (67) &middot; 1/25 ventilation (40) &middot; 1/50 Table 1004.5 <i>Exercise rooms</i> (20). No sheet reconciles them.",
"H-01r":"The 994 SF room whose classification drives exit count, egress width, fixtures and ventilation. See H-01 on G-1.",
"H-02":"Table 1006.2.1 row A/E/M sprinklered = <b>75 ft</b>. G-1 says 75, G-0 says 50. The two general sheets disagree.",
"H-03":"Door 104 scheduled at <b>2'-8&Prime;</b> &rarr; about 30-1/8&Prime; clear. FBC-B 1010.1.1 requires <b>32&Prime;</b>. G-0 and G-3 both say 32&Prime;.",
"M-01":"1005.3.2 Exc. 1 needs sprinklers <i>and</i> an EVACS. None shown. Base factor gives <b>14.0&Prime;</b>, not 10.50&Prime;.",
"M-02":"Exit capacities computed at <b>0.20</b> in/occ; required width at <b>0.15</b>. One analysis, two factors.",
"M-03":"Table 1604.5 sets RC III at assembly occupant load <b>&gt; 300</b>. At 70 (or 23), RC II applies.",
"M-04":"1,436 SF vs 1,375 SF, basis not stated. Level II <b>work area not identified</b> &mdash; FBC-EBC 601.2 / 603.1.",
"M-05":"FBC-B 1004.9 requires a permanent posted occupant-load sign in an assembly space. Not shown anywhere in the set.",
"M-06":"Note 5 calls for flame-spread ratings but <b>no Class A/B/C</b> is assigned. Table 803.13 sets them by space.",
"M-07":"Counter marked accessible on A-5 but <b>not dimensioned</b>. FBC-A 904.4.1: 36&Prime; max high, 36&Prime; min long.",
"M-08":"Two restroom floor drains, <b>no trap primer or barrier device</b> shown. FBC-P 1002.4.1.",
"L-01":"&lsquo;ACCESSORY / 0&rsquo; is not a Table 1004.5 function. Footnote it to 1004.2.1 instead.",
"L-02":"The 2 CFM row computes from the <b>13 SF closet</b>, not 204 SF of hall + restrooms. C-01 total unaffected.",
"V-01":"250 LF limit correct (Table 1017.2) and the 69'-4&Prime; path <b>measured at 68.9 ft</b>.",
"V-02":"1020.5 = 20 ft. The 50-ft sprinklered exception excludes Group A. Correctly applied.",
"V-03":"Table 1020.3 &lsquo;facilities not listed&rsquo; = 44&Prime;. Value <i>and</i> section number both check out.",
"V-04":"2 exits required and provided. Separation 71'-9&Prime; vs 24'-12&Prime; required (1007.1.1 Exc. 2).",
"V-05":"32&Prime; clear is the correct requirement &mdash; 1010.1.1 and AC 404.2.3 agree. Whether it is met: see H-03.",
"V-06":"Table 2902.1 row matches exactly; the 35/35 split and every round-up are correct. Holds at OL 23 too.",
"V-07":"Path 1 <b>68.9 ft</b> vs 69'-4&Prime; &middot; Path 2 <b>25.6 ft</b> vs 24'-7&Prime; &middot; common <b>9.5 ft</b> vs 8'-1&Prime; &middot; band <b>3'-8&Prime;</b> exact.",
"V-08":"Turning space, clear floor, alcoves and all four reach ranges checked against 304&ndash;308. <b>Every value correct.</b>",
"V-09":"604.2 through 604.7 and 609.4 all match. 18&Prime; centerline is at the code <i>maximum</i> &mdash; hold it in the field.",
"V-10":"606.3 rim 34&Prime;, 306.3 knee 27&Prime;, 306.2 toe 9&Prime;/17&ndash;25&Prime;, 603.3 mirror 40&Prime;. All correct.",
"V-11":"602.4 spout 36&Prime;, 602.5 5&Prime; from front edge, 30&times;48 clear floor. Hi-lo unit on A-5 satisfies 602.7.",
"V-12":"All six approach conditions match <b>Table 404.2.4.1 exactly</b>, including the +12&Prime; closer-and-latch rule.",
"V-13":"703.2 character height, 703.3.2 Braille, 703.4 mounting 48&ndash;60&Prime; and latch-side location all correct.",
"V-14":"303.2 &frac14;&Prime; / 303.3 &frac12;&Prime; at 1:2. Correct &mdash; and A-4 note 7 already flags the epoxy-to-tile transitions.",
"V-15":"Note 7 protects the U465 barriers; note 8 maintains exits and accessible routes through construction.",
"V-16":"W1 checked against the U465 text on A-11 item by item: studs, spacing, gauge and board all inside the listing.",
"V-17":"10'-0&Prime; ceilings vs the <b>7'-6&Prime;</b> minimum of 1003.2. Note 24 calls for emergency and exit lighting.",
"V-18":"Ceiling heights and types match A-3 room for room; wet-area tile matches the A-2 backer-board note.",
"V-19":"Notes 6 and 7 keep equipment out of egress paths and accessible clearances. Extinguishers and fountain correct.",
"V-20":"60&Prime; turning space, 60&Prime;/56&Prime; WC clearance and 30&times;48 clear floors all dimensioned. 213.2 Exc. 4 satisfied.",
"V-21":"Notes 3&ndash;8 set the right rules: 48&Prime; max reach, 30&times;48 clear floor, 60&Prime; turning, one-hand operation.",
"V-22":"Suspended fixtures at 11'-0&Prime; clear the 80&Prime; headroom rule. Shelf projections sit off the circulation path.",
"V-23":"Three exit/emergency combo units shown, consistent with the RCP and E-2. Suspended loads on independent support.",
"V-24":"Rated section matches U465 on all four parameters. Deflection track and clip angles correctly detailed.",
"V-25":"Design No. U465, 1 HR, reproduced in full <i>as live text</i> &mdash; which is what let W1 be checked line by line.",
"V-26":"Six firestop systems named by number for the actual penetration and joint conditions. Details are raster only.",
"V-27":"Hangers, transitions, elbows and vane locations follow SMACNA; support matches the A-3 independent-support notes.",
"V-28":"GFI at restrooms, roof and exterior (210.8(B)); dedicated RTU service receptacle (210.63); show window per 210.62.",
"V-29":"Type G and H emergency fixtures scheduled; occupancy sensing provided; infrared relays at 72% of a 30 A circuit.",
"V-30":"Per-phase column resummed: <b>62.36 kVA = 173 A</b> on a 250 A panel off a 1000 A service. 69% loaded.",
"V-31":"Circuit 14 pairs emergency with normal lighting &mdash; which is exactly what <b>NEC 700.12</b> requires for unit equipment.",
"V-32":"WC/LAV/DF/MS match the G-1 count. ~14 DFU on a 4&Prime; drain; 3&Prime; branch is at its 2-water-closet limit.",
"V-33":"1&Prime; &rarr; &frac34;&Prime; &rarr; &frac12;&Prime; steps down conventionally. Point-of-use heaters remove the hot-water run entirely.",
"V-35":"Outdoor-air math recomputed line by line and <b>correct</b>. Duct smoke detection provided; +140 CFM pressurization intentional.",
"V-36":"Counter section leaves the underside free, so 27&Prime; knee clearance is achievable. Fabricator note is in writing.",
"V-34":"4&Prime; VTR; every branch vent at or above half the drain it serves (906.1). Cleanouts and hangers correctly detailed.",
}
