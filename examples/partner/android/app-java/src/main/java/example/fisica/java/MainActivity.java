package example.fisica.java;

import android.Manifest;
import android.app.Activity;
import android.bluetooth.BluetoothDevice;
import android.bluetooth.BluetoothManager;
import android.content.pm.PackageManager;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;
import com.carenco.fisica.*;
import kotlin.Unit;
import java.util.HashSet;

public class MainActivity extends Activity {
    /**
     * Paste an SDK key from the developer portal for full mode; empty runs in basic mode. The key is
     * bound to this app's package name, one seat for every install, so it ships inside the app
     * (pairing with a code is for PCs).
     */
    private static final String SDK_KEY = "";
    /** Activates the key for this app and renews it (needs the INTERNET permission). */
    private static final String PORTAL = "https://developer.fisicahealthcare.com";
    private TextView status;
    private TextView licenseText;
    private FisicaCore core;
    private FisicaEntitlement license;
    private LinearLayout devices;
    private BleTransport ble;
    private FisicaClient client;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final HashSet<String> seen = new HashSet<>();
    private String[] permissions() {
        return Build.VERSION.SDK_INT >= 31
            ? new String[]{Manifest.permission.BLUETOOTH_SCAN, Manifest.permission.BLUETOOTH_CONNECT}
            : new String[]{Manifest.permission.ACCESS_FINE_LOCATION};
    }
    @Override public void onCreate(Bundle saved) {
        super.onCreate(saved);
        LinearLayout layout = new LinearLayout(this); layout.setOrientation(LinearLayout.VERTICAL);
        status = new TextView(this); status.setText("Grant Bluetooth access, scan, then choose a Scale");
        licenseText = new TextView(this);
        devices = new LinearLayout(this); devices.setOrientation(LinearLayout.VERTICAL);
        Button scan = new Button(this); scan.setText("Scan BLE"); scan.setOnClickListener(view -> {
            for (String permission : permissions()) {
                if (checkSelfPermission(permission) != PackageManager.PERMISSION_GRANTED) { requestPermissions(permissions(), 1); return; }
            }
            scan();
        });
        layout.addView(licenseText); layout.addView(status); layout.addView(scan); layout.addView(devices); setContentView(layout);
        core = new FisicaCore(null, message -> { System.err.println(message); return Unit.INSTANCE; });
        // Basic mode without a key: measurement, weight and foot size; one frame a second, no raw data.
        license = new FisicaEntitlement(core, PORTAL, getFilesDir());
        license.setOnStatus(current -> { runOnUiThread(this::showLicense); return Unit.INSTANCE; });
        if (!SDK_KEY.isEmpty()) license.importToken(SDK_KEY);
        showLicense();
        BluetoothManager manager = getSystemService(BluetoothManager.class);
        ble = new BleTransport(this, manager == null ? null : manager.getAdapter());
        ble.setOnError(error -> { status.setText(error.getMessage()); return Unit.INSTANCE; });
        ble.setOnDevice(result -> {
            BluetoothDevice device = result.getDevice();
            String name = device.getName();
            if (name != null && name.contains(FisicaBleProfile.NAME_CONTAINS) && seen.add(device.getAddress())) {
                Button choose = new Button(this); choose.setText(name + ": " + device.getAddress());
                choose.setOnClickListener(view -> measure(device)); devices.addView(choose);
            }
            return Unit.INSTANCE;
        });
    }
    private void showLicense() {
        EntitlementStatus current = license.status();
        String code = current.getActivationCode();
        licenseText.setText("License: " + current + (code == null ? "" : "\nActivation code: " + code));
    }
    private void scan() {
        seen.clear(); devices.removeAllViews(); ble.scan(); status.setText("Scanning for 5 seconds");
        main.postDelayed(() -> ble.stopScan(), 5000);
    }
    @Override public void onRequestPermissionsResult(int request, String[] permissions, int[] grants) {
        super.onRequestPermissionsResult(request, permissions, grants);
        if (request == 1 && grants.length > 0) {
            for (int grant : grants) if (grant != PackageManager.PERMISSION_GRANTED) { status.setText("Bluetooth permission denied"); return; }
            scan();
        }
    }
    private void failed(Throwable error, FisicaClient current) {
        status.setText(error.getMessage()); current.close((event, failure) -> { client = null; return Unit.INSTANCE; });
    }
    private void measure(BluetoothDevice device) {
        if (client != null) return;
        ble.stopScan(); ble.select(device);
        FisicaClient current = new FisicaClient(core, ble, license, runnable -> runOnUiThread(runnable));
        client = current;
        current.setOnError(error -> { failed(error, current); return Unit.INSTANCE; });
        current.connect(FisicaLink.BLE, 15000, (event, error) -> {
            if (error != null) { failed(error, current); return Unit.INSTANCE; }
            current.start(null, (started, startError) -> {
                if (startError != null) { failed(startError, current); return Unit.INSTANCE; }
                status.setText("Measuring for 5 seconds");
                main.postDelayed(() -> current.stop((stopped, stopError) -> {
                    try {
                        if (stopError != null) { status.setText(stopError.getMessage()); }
                        else {
                            SessionReport report = current.report();
                            status.setText(report == null ? "No stable result" : "Weight: " + report.getWeight() + " kg; frames: "
                                + report.getFrameCount() + " (" + license.status().getModeName() + " mode)");
                        }
                    } finally { current.close((closed, closeError) -> { client = null; return Unit.INSTANCE; }); }
                    return Unit.INSTANCE;
                }), 5000);
                return Unit.INSTANCE;
            });
            return Unit.INSTANCE;
        });
    }
    @Override public void onDestroy() {
        main.removeCallbacksAndMessages(null); ble.stopScan();
        if (client != null) client.close((event, error) -> Unit.INSTANCE);
        license.close();
        super.onDestroy();
    }
}
