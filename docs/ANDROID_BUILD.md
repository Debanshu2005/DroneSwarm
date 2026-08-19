# Building the PhoneOS Android APK

The PhoneOS mobile application can be compiled directly into a native Android `.apk` file using Capacitor and Gradle.

## Prerequisites
To compile the APK, the host machine must have the following installed and configured:
1. **Node.js**: (Version 18+ required)
2. **Java Development Kit**: OpenJDK 17 or higher.
3. **Android SDK**: Command-line tools (platform-tools, platforms;android-34, build-tools;34.0.0).

## Build Instructions

1. **Install JavaScript Dependencies**:
   ```bash
   cd PhoneOS/mobile
   npm install
   ```

2. **Compile the Static Web Assets**:
   ```bash
   npm run build
   ```
   This generates the HTML, CSS, and JS in the `dist` directory.

3. **Build the Debug APK**:
   ```bash
   npm run android:debug
   ```
   This command automatically syncs the Vite `dist` folder into the Capacitor Android project and uses the Gradle wrapper (`./gradlew assembleDebug`) to compile the APK.

4. **Build the Release APK**:
   ```bash
   npm run android:release
   ```
   This performs a similar sync but runs `./gradlew assembleRelease` instead.

## APK Location
After a successful build, the generated Android package will be available at:
`PhoneOS/mobile/android/app/build/outputs/apk/debug/app-debug.apk`

## Common Errors
- `Capacitor CLI requires NodeJS >=22.0.0`: The project uses Capacitor v6 for backwards compatibility with Node 18, ensuring you do not encounter this engine issue.
- `JAVA_HOME not set`: The custom `android:debug` script automatically sets `JAVA_HOME` and `ANDROID_HOME` pointing to local directory installations if they are missing in your root shell.
