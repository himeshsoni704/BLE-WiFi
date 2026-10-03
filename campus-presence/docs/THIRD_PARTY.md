# Third-party repositories, datasets and libraries

Everything below was inspected on 2026-10-01 by shallow-cloning each repository
(`git clone --depth 1`) and reading its README, license information and the files
named in the "What I read" column. I did **not** read every file in every repo.

## Policy

* **No source code from these repositories is copied into this project.** Where a
  repository informed a technique, the technique was re-implemented from scratch and
  the table says so. That makes the licence question moot for the code, but the
  attribution is kept because the ideas are theirs.
* Repositories with **no licence file** are "all rights reserved" by default. They were
  read for ideas only, and nothing from them (code, data, images) is redistributed here.
* Official Android APIs were preferred over third-party BLE/Wi-Fi libraries. The
  Android app (`campus-presence/android`) uses only Android platform APIs (`android.bluetooth.le`,
  `WifiManager`, `HttpURLConnection`, `org.json`) and has no AndroidX, Retrofit or OkHttp
  dependency. Its tests use JUnit and the `org.json` reference implementation.

## Repositories

| Repository | Commit inspected | Licence as found | What I read | What it informed | Copied? |
|---|---|---|---|---|---|
| [android/platform-samples](https://github.com/android/platform-samples) | `0445045` (2026-09-30) | **Apache-2.0** (`LICENSE`) | `samples/connectivity/bluetooth/ble/` — `FindBLEDevicesSample.kt`, `server/GATTServerSampleService.kt`, its `AndroidManifest.xml` | The Android 12+ permission set (`BLUETOOTH_SCAN`, `BLUETOOTH_ADVERTISE`, `BLUETOOTH_CONNECT`, location), the `ScanSettings.Builder` / `AdvertiseSettings.Builder` call pattern, and foreground-service-type declarations. I wrote my own scanner/advertiser around those documented APIs. The repo has **no Wi-Fi scanning sample** (searched `samples/` for wifi/rtt), so Wi-Fi scanning follows the public `WifiManager` docs instead. | No |
| [sharan-naribole/wlan_localization](https://github.com/sharan-naribole/wlan_localization) | `5e1949d` (2026-01-16) | **MIT** (`LICENSE`, © 2026 Sharan Naribole) | `README.md`, `src/` layout | Handling the sparse fingerprint problem (most APs unseen at any location) by filling "not heard" with a floor value, and the idea of cascading coarse-to-fine classification. Our model is a flat room classifier, so only the missing-value convention was adopted. | No |
| [thm-msror/Indoor-WiFi-Localization](https://github.com/thm-msror/Indoor-WiFi-Localization) | `c871208` (2026-08-27) | **None found** (no licence file; no licence text in any file) | `README.md`, `Code/` file list | Preprocessing conventions on UJIndoorLoc: replace the "undetected" placeholder with a very low dBm value, drop zero-variance APs. Their pipeline (Ridge / Keras MLP / PCA) is not used. | No |
| [Takaklas/Wifi-Indoor-Location](https://github.com/Takaklas/Wifi-Indoor-Location) | `09ed3b4` (2019-05-16) | **None found** | `README.md`, `fingerprinting.py`, `localization_fingerprinting.py` (grep for the classifier) | Confirms the standard recipe: survey labelled positions into a CSV, then k-NN (they use k=2) over the RSSI vector. Our survey mode and KNN option follow that recipe, independently implemented. | No |
| [RiccardoM3/IndoorPositioning](https://github.com/RiccardoM3/IndoorPositioning) | `09095f2` (2023-04-17) | **MIT** (`LICENSE`, © 2023 Riccardo Menon) | `README.md`, file list | The survey workflow: stand at a labelled reference point, refresh Wi-Fi RSSI ~50 times, upload. Our "survey" screen/endpoint does the same thing. Their claim that disconnecting from Wi-Fi makes each scan 1–2 s faster is **their empirical observation, unverified by me**. | No |
| [P3st83/Bluetooth-Indoor-Positioning](https://github.com/P3st83/Bluetooth-Indoor-Positioning) | `98215ac` (2025-06-14) | **MIT declared in `README.md`**; there is **no `LICENSE` file** and no copyright line | `README.md`, `docs/positioning_algorithm.md`, file list (`server/algorithms/`) | The weighted-centroid idea (weights from RSSI) and the log-distance path-loss formula `RSSI = -10·n·log10(d) + A`. The path-loss formula is textbook and is used (re-derived) in the synthetic Wi-Fi/BLE generator. | No |
| [xRanKhx/ble-position](https://github.com/xRanKhx/ble-position) | `c42a779` (2026-09-19) | **MIT** (`LICENSE`) | `README.md`, `custom_components/` file list, grep for KNN/EMA options | BLE fingerprinting with k-NN plus EMA smoothing as tunable options, and "auto-calibrate during standstill". Home-Assistant-specific code is not reusable here. | No |
| [WiFiLocalization/BLE_Wi-Fi_RSSI_SQI_Indoor_Localization](https://github.com/WiFiLocalization/BLE_Wi-Fi_RSSI_SQI_Indoor_Localization) | `15eeb96` (2024-05-28) | **None found**; README asks to cite the paper below | `README.md`, zip file listings (4 zips, 100–320 MB each) | Dataset description only. It is measured **router-side** (the README says "not measured at client side"), so it is a different measurement setup from phone-side fingerprinting. **Not downloaded into this repo, not redistributed, not used for training.** | No |

Citation requested by the dataset authors (not used, listed for completeness):
Yuen, Bie, Cairns, Harper, Xu, Chang, Dong, Lu, "Wi-Fi and Bluetooth contact tracing
without user intervention", *IEEE Access* 10, 91027–91044, 2022.

## Techniques with no single source

* HMAC-SHA256 rotating tokens, replay-resistant nonces and timestamp windows:
  standard constructions; the wire format is this project's own.
* Isolation Forest: Liu, Ting, Zhou, ICDM 2008, via scikit-learn.
* Log-distance path-loss + log-normal shadowing for the synthetic Wi-Fi/BLE RSSI.

## Runtime dependencies

Licences below are as I know them for the upstream projects; confirm against each
package before redistributing.

| Component | Used for | Licence (upstream) |
|---|---|---|
| FastAPI, Starlette, Uvicorn | backend | MIT / MIT / BSD-3 |
| SQLAlchemy | ORM | MIT |
| PyJWT | JWT auth | MIT |
| scikit-learn, NumPy, SciPy, joblib | Wi-Fi model, Isolation Forest, RAG | BSD-3 |
| google-genai | Gemini provider | Apache-2.0 |
| React, Vite, Tailwind CSS, Recharts | dashboard | MIT |
| AndroidX (Compose, Room, Lifecycle, Core), Kotlin coroutines | Android app | Apache-2.0 |
| Retrofit, OkHttp, Gson | Android networking | Apache-2.0 |
| NimBLE-Arduino, ArduinoJson (ESP32 sketch) | classroom node | Apache-2.0 / MIT |

## Data in this repository

All datasets here are **synthetic**, generated by `ml/generate_dataset.py`. The seed
"verified cases" for RAG are **authored examples**, labelled `origin = seed_demo`, not real
faculty decisions.
