plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "vn.subvideo.remote"
    compileSdk = 34

    defaultConfig {
        applicationId = "vn.subvideo.remote"
        minSdk = 24
        targetSdk = 34
        versionCode = 1
        versionName = "2.3.0"
    }
    // Khoá ký cố định (đi kèm mã nguồn) -> bản APK mới cài ĐÈ được lên bản cũ, giữ nguyên máy đã ghép.
    signingConfigs {
        create("release") {
            storeFile = file("subvideo-release.p12")
            storePassword = "subvideo-app"
            keyAlias = "subvideo"
            keyPassword = "subvideo-app"
            storeType = "pkcs12"
        }
    }
    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = signingConfigs.getByName("release")
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
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.activity:activity-ktx:1.9.2")
    implementation("androidx.work:work-runtime-ktx:2.9.1")
    implementation("com.journeyapps:zxing-android-embedded:4.3.0")
}
