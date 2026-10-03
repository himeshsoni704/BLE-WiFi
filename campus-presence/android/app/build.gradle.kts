plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.campuspresence.app"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.campuspresence.app"
        minSdk = 26
        targetSdk = 34
        versionCode = 1
        versionName = "0.1"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
}

dependencies {
    // Deliberately nothing here: no AndroidX, no Retrofit/OkHttp. The app uses platform APIs only
    // (android.bluetooth.le, WifiManager, HttpURLConnection, org.json).
    testImplementation("junit:junit:4.13.2")
    // The real org.json. The copy inside android.jar is a stub that returns defaults in local unit tests.
    testImplementation("org.json:json:20240303")
}
