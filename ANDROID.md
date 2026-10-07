# EventFlow Android app (Android Studio)

The quickest way to ship EventFlow on Android is a **WebView app** that opens your hosted EventFlow
(for the demo: the `https://….trycloudflare.com` link printed by `demo-online.bat`). Every screen, animation, the fest
picker, checkout and the intro already work on phones. This guide gives a WebView setup that also handles the
things a plain WebView can't do on its own:

| Feature on the site | What the app must do (already in the code below) |
|---|---|
| **Save as PDF** (receipt, certificate) | `window.print()` does nothing in a WebView, so the site calls `EventFlowAndroid.printPage()`. The app opens Android's print dialog → *Save as PDF*. |
| **UPI payment** QR / "Open UPI app" (`upi://pay…`) | Hand `upi:`, `tel:`, `mailto:` links to the phone so GPay / PhonePe / Paytm open. |
| **Uploads** (posters, videos, PDFs, payment screenshot, profile photo) | Show Android's file picker (`onShowFileChooser`). |
| **QR check-in scanner** (camera) | Grant the camera to the page (`onPermissionRequest`). Needs **https**, which the Cloudflare link gives you. |
| Links to other websites | Open in the phone's browser; EventFlow pages stay in the app. |
| Back button | Goes back a page instead of closing the app. |
| App detection | The app adds `EventFlowApp` to its user agent; the site then marks `<html class="in-app">`. |

## 1. Create the project

Android Studio → **New Project → Empty Views Activity** → Language **Kotlin**, Minimum SDK **24**.
Package name used below: `com.techknights.eventflow` (change it to yours).

## 2. `app/src/main/AndroidManifest.xml`

```xml
<manifest xmlns:android="http://schemas.android.com/apk/res/android">
    <uses-permission android:name="android.permission.INTERNET" />
    <uses-permission android:name="android.permission.CAMERA" />
    <uses-feature android:name="android.hardware.camera" android:required="false" />

    <application
        android:label="EventFlow"
        android:icon="@mipmap/ic_launcher"
        android:theme="@style/Theme.AppCompat.DayNight.NoActionBar"
        android:usesCleartextTraffic="true">  <!-- only needed if you test against http://<PC-IP>:5000 -->
        <activity
            android:name=".MainActivity"
            android:exported="true"
            android:configChanges="orientation|screenSize|screenLayout|keyboardHidden|uiMode"
            android:windowSoftInputMode="adjustResize">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>
    </application>

    <!-- lets the app see which UPI apps are installed (Android 11+) -->
    <queries>
        <intent>
            <action android:name="android.intent.action.VIEW" />
            <data android:scheme="upi" />
        </intent>
    </queries>
</manifest>
```

## 3. `app/build.gradle(.kts)` dependencies

```kotlin
dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("androidx.activity:activity-ktx:1.9.2")
}
```

## 4. `MainActivity.kt`

Put your link in `BASE_URL` (keep the trailing `/`).

```kotlin
package com.techknights.eventflow

import android.Manifest
import android.content.ActivityNotFoundException
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Bundle
import android.print.PrintAttributes
import android.print.PrintManager
import android.webkit.*
import android.widget.Toast
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat

class MainActivity : AppCompatActivity() {

    companion object {
        const val BASE_URL = "https://YOUR-LINK.trycloudflare.com/"
    }

    private lateinit var web: WebView
    private var fileCallback: ValueCallback<Array<Uri>>? = null
    private var pendingPermission: PermissionRequest? = null

    private val pickFiles = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { res ->
        fileCallback?.onReceiveValue(WebChromeClient.FileChooserParams.parseResult(res.resultCode, res.data))
        fileCallback = null
    }

    private val askCamera = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        pendingPermission?.let { if (granted) it.grant(it.resources) else it.deny() }
        pendingPermission = null
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        web = WebView(this)
        setContentView(web)

        web.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true                  // theme, intro-once, drafts
            mediaPlaybackRequiresUserGesture = false  // teaser videos in posts
            userAgentString = "$userAgentString EventFlowApp/1.0"
        }
        CookieManager.getInstance().setAcceptCookie(true)
        CookieManager.getInstance().setAcceptThirdPartyCookies(web, true)
        web.addJavascriptInterface(Bridge(), "EventFlowAndroid")

        web.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView?, request: WebResourceRequest?): Boolean {
                val url = request?.url ?: return false
                if (url.scheme == "http" || url.scheme == "https") {
                    if (url.host == Uri.parse(BASE_URL).host) return false   // stay inside the app
                }
                openOutside(url)                                             // upi:, tel:, mailto:, other sites
                return true
            }
        }

        web.webChromeClient = object : WebChromeClient() {
            override fun onShowFileChooser(view: WebView?, callback: ValueCallback<Array<Uri>>?,
                                           params: FileChooserParams?): Boolean {
                fileCallback?.onReceiveValue(null)
                fileCallback = callback
                val intent = params?.createIntent() ?: return false
                if (params?.mode == FileChooserParams.MODE_OPEN_MULTIPLE) intent.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true)
                return try { pickFiles.launch(intent); true } catch (e: ActivityNotFoundException) { fileCallback = null; false }
            }

            override fun onPermissionRequest(request: PermissionRequest?) {
                request ?: return
                runOnUiThread {
                    val ok = ContextCompat.checkSelfPermission(this@MainActivity, Manifest.permission.CAMERA) ==
                            PackageManager.PERMISSION_GRANTED
                    if (ok) request.grant(request.resources)
                    else { pendingPermission = request; askCamera.launch(Manifest.permission.CAMERA) }
                }
            }
        }

        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() { if (web.canGoBack()) web.goBack() else finish() }
        })

        if (savedInstanceState == null) web.loadUrl(BASE_URL) else web.restoreState(savedInstanceState)
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        web.saveState(outState)
    }

    private fun openOutside(uri: Uri) {
        try {
            startActivity(Intent(Intent.ACTION_VIEW, uri))
        } catch (e: ActivityNotFoundException) {
            val msg = if (uri.scheme == "upi") "No UPI app found. Pay by scanning the QR from another phone." else "No app can open this link."
            Toast.makeText(this, msg, Toast.LENGTH_LONG).show()
        }
    }

    /** Called from the site: window.EventFlowAndroid.printPage(title) */
    inner class Bridge {
        @JavascriptInterface
        fun printPage(title: String) {
            runOnUiThread {
                val pm = getSystemService(PRINT_SERVICE) as PrintManager
                pm.print(title, web.createPrintDocumentAdapter(title), PrintAttributes.Builder().build())
            }
        }
    }
}
```

## 5. Run it

1. On your PC run **`demo-online.bat`** and copy the `https://….trycloudflare.com` link into `BASE_URL`.
2. Plug in your phone (USB debugging on) or start an emulator, then press **Run ▶** in Android Studio.
3. For an installable file: **Build → Build App Bundle(s) / APK(s) → Build APK(s)**, then share the `.apk`.

The free Cloudflare link changes every time `demo-online.bat` starts. For a fixed address, set up a named Cloudflare
Tunnel with your own domain, or host EventFlow on a server, and put that URL in `BASE_URL`.

## Going native later

Every action the UI performs is also available as JSON under `/api/…` (likes, saves, follows, friends, comments,
messages, counts, search, coupon check, assistant). Fests are available at **`GET /api/events/<id>`**: tracks, pricing
(`event` or `pass`), each event's fee, team size and seats left, whether the signed-in student is already registered,
and the `register_url` to post picks to (`pick=<event id>` repeated, plus `team_<event id>` for team events).
