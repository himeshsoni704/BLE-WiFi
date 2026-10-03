<!--
Knowledge base for the anomaly explainer. One passage per "## ID | Title" heading, then a `tags:` line, then plain text.
Retrieval matches a passage's tags against the tags computed for an anomaly (rule_*, feat_*, wifi_conf_low, ...) and its
words against the text, so keep passages short and specific. A passage with `always: yes` is sent with every explanation.
The explainer may only cite passages by ID; anything it says that is not in the evidence, a similar past case or a
passage here is treated as unsupported. Add your own campus policy as new passages.
-->

## KB-POLICY | What the system and the explainer may and may not do
always: yes
tags: policy review faculty decision
The system raises a record for human review. It never marks a student absent and never decides misconduct. The explainer
only describes the evidence and lists possible causes with how likely each looks; faculty decide. Confirming or dismissing
an anomaly stores it as a verified case that later explanations may cite as a similar case. This does not retrain the model.

## KB-FUSION | How an attendance state is decided
tags: fusion score present likely_present review_required absent families
Each student gets a weighted score: classroom Bluetooth 35, Wi-Fi match 30, peer consistency 20, sustained presence 10,
face 5, RFID 5. PRESENT needs a score of at least 70 and at least two independent signal families. LIKELY_PRESENT starts
at 50 and REVIEW_REQUIRED at 30. The score is a transparent checklist, not a probability.

## KB-IF-001 | What an Isolation Forest flag means
tags: isoforest_flagged isolation forest unusual
The Isolation Forest scores how unusual a record's combination of 12 features is compared with the normal behaviour it was
trained on. A flag means "unusual", not "wrong". Unusual records can be innocent, such as a phone left in a bag or a
student who left early. It only runs when at least 3 nearby devices were seen, because it was trained at classroom density.

## KB-IF-002 | Reading the feature attribution
tags: attribution shapley feature drivers
The attribution splits the forest's score among the features using exact Shapley values against a typical-normal baseline
(the training median). A positive share means that feature pushed the score toward unusual. The shares describe this
model's score for this record; they do not prove why the student behaved that way. Values like -100 dBm, 3600 s or 30 dB
are placeholders meaning "nothing was measured".

## KB-IF-003 | Features the forest cannot see well
tags: feat_token_reuse_count feat_session_switch_count weak_feature rule_token_reuse
Some features almost never vary in training, such as how many other devices saw the token. An Isolation Forest cannot
isolate what never varied, so it may miss a pattern in them. The deterministic rules cover these patterns instead, which is
why both the rules and the forest run.

## KB-RULE-BLEWIFI | Bluetooth and Wi-Fi disagree
tags: rule_ble_wifi_contradiction signal_consistency feat_signal_consistency wifi_conf_low room_adjacent
This rule fires when the classroom Bluetooth marker places the student in the session room but at least 60% of at least 2
Wi-Fi readings place them in a different room. Bluetooth is a verified marker detection; the Wi-Fi room is a model
estimate and is the weaker of the two.

## KB-RULE-TOKEN | The same temporary token seen in other rooms
tags: rule_token_reuse rule_abnormal_token_reuse token_reuse_multi_device feat_token_reuse_count
Each phone broadcasts a token that changes every 30 seconds. This rule fires when devices in other rooms heard a student's
token while that student's own evidence is in the session room. Three or more such devices make it high severity. A forwarded
live token could produce this pattern, so it is worth checking; a rule hit alone is not proof of forwarding.

## KB-RULE-MOVE | Movement faster than a person can walk
tags: rule_impossible_movement speed_extreme speed_high feat_estimated_speed feat_time_between_locations
This rule fires when two marker detections in different rooms imply a speed above 4 m/s, using the distance between the
rooms. It can come from a real second device or person, or from a phone clock that is wrong by up to the 2 minutes the
server tolerates, which shrinks the time between detections.

## KB-RULE-SWITCH | Several classrooms within a few minutes
tags: rule_rapid_session_switching feat_session_switch_count feat_number_of_classrooms room_adjacent
This rule fires when markers from more than 2 different classrooms were heard within 5 minutes. Walking down a corridor past
neighbouring classrooms can produce it for a student who attended the right class.

## KB-CAUSE-WIFI | Why a Wi-Fi room estimate can be wrong
tags: wifi_conf_low wifi_conf_very_low wifi_cached_scan room_adjacent rule_ble_wifi_contradiction feat_wifi_confidence feat_signal_consistency
Wi-Fi room estimates come from a model of signal strengths and are most often wrong by one neighbouring room. A weak or
restarted access point, an unsurveyed room, or an old cached scan (Android limits how often it scans) can make the estimate
lag behind where the student really is. A low confidence value means the model itself was unsure.

## KB-CAUSE-CLOCK | Phone clock errors
tags: clock_skew_suspected rule_impossible_movement speed_extreme
Readings carry the phone's own timestamp. A phone clock that is off by some tens of seconds moves detections in time and can
make a short walk look instantaneous. The server rejects readings more than 2 minutes from its clock, so smaller errors pass.

## KB-CAUSE-MARKER | Classroom marker not heard
tags: marker_missing peers_consistent feat_mean_ble_rssi feat_ble_duration
If the classroom marker stops broadcasting, for example from a flat battery, students show Wi-Fi and peer evidence for the right
room but no marker detections after the outage. A faculty check of the marker device settles it.

## KB-CAUSE-EARLY | Short or late presence
tags: short_presence feat_ble_duration feat_nearby_device_count
Bluetooth time in the room is measured against the time since the session started. A student who arrived late or left early,
or a session that has only just begun, has a low share of coverage. That is expected and not by itself a concern.

## KB-CAUSE-TWINS | Phones that move together
tags: device_cluster peer_rssi_very_strong feat_peer_rssi_max feat_rssi_twin_distance
Two phones whose marker readings track each other within a few dB, with very strong mutual signal for the whole session, may
be carried by one person. Friends who sit side by side can look similar for a while, so look at whether it lasts the whole
session and whether both phones also show other evidence of their own.

## KB-LIMIT-RELAY | What tokens cannot prevent
tags: rule_token_reuse relay token_reuse_multi_device
A temporary token proves a phone knows its secret, not where the phone is. A token forwarded live within its 30 second window
passes the token check, which is why the reuse and contradiction rules exist. Nothing here proves intent.

## KB-LIMIT-RSSI | Bluetooth signal strength is a coarse hint
tags: ble_rssi feat_mean_ble_rssi feat_peer_rssi_max peer_rssi_very_strong
Signal strength depends on body position, phone orientation and model. It is used as a rough proximity hint and is never turned
into a distance.
