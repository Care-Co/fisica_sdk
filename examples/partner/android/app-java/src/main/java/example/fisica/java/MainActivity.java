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
import android.view.View;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.RadioButton;
import android.widget.RadioGroup;
import android.widget.ScrollView;
import android.widget.TextView;
import com.carenco.fisica.*;
import kotlin.Unit;
import org.json.JSONException;
import org.json.JSONObject;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.Iterator;
import java.util.List;

/**
 * The same steps as every partner sample: SDK and core version, the license, pick a Scale (or the
 * synthetic demo), Connect (reads the battery), Measure (5 s), the report, Disconnect.
 */
public class MainActivity extends Activity {
    /**
     * Paste an SDK key from the developer portal for full mode; empty runs in basic mode. The key is
     * bound to this app's package name, one seat for every install, so it ships inside the app
     * (pairing with a code is for PCs).
     */
    private static final String SDK_KEY = "";
    /** Activates the key for this app and renews it (needs the INTERNET permission). */
    private static final String PORTAL = FisicaEntitlement.DEFAULT_ENDPOINT;
    private static final String NO_RESULT = "No stable result; check the load on the Scale and the received frames";
    private TextView licenseText;
    private TextView status;
    private Button scanButton;
    private Button demoButton;
    private RadioGroup devices;
    private Button connectButton;
    private Button measureButton;
    private TextView result;
    private Button disconnectButton;
    private FisicaCore core;
    private FisicaEntitlement license;
    private BleTransport ble;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final HashMap<Integer, BluetoothDevice> found = new HashMap<>();
    /** The Scale to connect: one found over BLE, or the synthetic demo Scale (no hardware). */
    private BluetoothDevice bleChoice;
    private MockTransport demoChoice;
    private FisicaClient client;
    private boolean connected;
    private boolean busy;

    private String[] permissions() {
        return Build.VERSION.SDK_INT >= 31
            ? new String[]{Manifest.permission.BLUETOOTH_SCAN, Manifest.permission.BLUETOOTH_CONNECT}
            : new String[]{Manifest.permission.ACCESS_FINE_LOCATION};
    }
    private Button button(String label, View.OnClickListener action) {
        Button button = new Button(this);
        button.setText(label);
        button.setOnClickListener(action);
        return button;
    }
    @Override public void onCreate(Bundle saved) {
        super.onCreate(saved);
        licenseText = new TextView(this);
        status = new TextView(this);
        status.setText("Scan for Scales, or try the demo");
        scanButton = button("Scan for Scales", view -> {
            for (String permission : permissions()) {
                if (checkSelfPermission(permission) != PackageManager.PERMISSION_GRANTED) { requestPermissions(permissions(), 1); return; }
            }
            scan();
        });
        demoButton = button("Demo without hardware", view -> chooseDemo());
        devices = new RadioGroup(this);
        devices.setOnCheckedChangeListener((group, id) -> {
            BluetoothDevice device = found.get(id);
            if (device == null) return;
            bleChoice = device; demoChoice = null;
            status.setText(device.getName() + " selected; press Connect"); refresh();
        });
        connectButton = button("Connect", view -> connect());
        measureButton = button("Measure (5 s)", view -> measure());
        result = new TextView(this);
        disconnectButton = button("Disconnect", view -> release("Disconnected"));
        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        layout.setPadding(32, 32, 32, 32);
        for (View view : new View[]{licenseText, status, scanButton, demoButton, devices, connectButton, measureButton, result, disconnectButton}) {
            layout.addView(view);
        }
        ScrollView scroll = new ScrollView(this);
        scroll.addView(layout);
        setContentView(scroll);
        showReport(null); refresh();
        try {
            core = new FisicaCore(null, message -> { System.err.println(message); return Unit.INSTANCE; });
            // Basic mode without a key: measurement, weight and foot size; one frame a second, no raw data.
            license = new FisicaEntitlement(core, PORTAL);  // the key is kept in the app's files by default
            license.setOnStatus(current -> { runOnUiThread(this::showLicense); return Unit.INSTANCE; });
            if (!SDK_KEY.isEmpty()) license.importToken(SDK_KEY);
            showLicense();
        } catch (RuntimeException | LinkageError error) {
            status.setText("Error: " + error.getMessage()); scanButton.setEnabled(false); demoButton.setEnabled(false);
            return;
        }
        BluetoothManager manager = getSystemService(BluetoothManager.class);
        ble = new BleTransport(this, manager == null ? null : manager.getAdapter());
        ble.setOnError(error -> { status.setText("Error: " + error.getMessage()); return Unit.INSTANCE; });
        ble.setOnDevice(scan -> {
            BluetoothDevice device = scan.getDevice();
            String name = device.getName();
            if (name != null && name.contains(FisicaBleProfile.NAME_CONTAINS) && !found.containsValue(device)) {
                RadioButton option = new RadioButton(this);
                option.setId(View.generateViewId());
                option.setText(name + ": " + device.getAddress());
                found.put(option.getId(), device);
                devices.addView(option);
            }
            return Unit.INSTANCE;
        });
        refresh();
    }
    private void showLicense() {
        EntitlementStatus current = license.status();
        // "full mode", or "basic mode (no_key)" with the SDK's explanation of the reason.
        StringBuilder text = new StringBuilder("Fisica SDK " + FisicaRelease.SDK_VERSION + " (core " + core.getVersion() + ")");
        text.append("\nLicense: ").append(current.getModeName()).append(" mode");
        if (current.getReasonName() != null) text.append(" (").append(current.getReasonName()).append(")");
        if (current.getMessage() != null) text.append("\n").append(current.getMessage());
        if (current.getActivationCode() != null) text.append("\nActivation code: ").append(current.getActivationCode());
        licenseText.setText(text);
    }
    private void refresh() {
        scanButton.setEnabled(client == null); demoButton.setEnabled(client == null);
        for (int i = 0; i < devices.getChildCount(); i++) devices.getChildAt(i).setEnabled(client == null);
        connectButton.setEnabled((bleChoice != null || demoChoice != null) && client == null && !busy);
        measureButton.setEnabled(connected && !busy);
        disconnectButton.setEnabled(client != null && !(busy && connected));
    }
    private void scan() {
        devices.clearCheck(); found.clear(); devices.removeAllViews(); bleChoice = null;
        ble.scan(); status.setText("Scanning for 5 seconds"); refresh();
        main.postDelayed(() -> {
            ble.stopScan();
            if (client == null) status.setText(found.isEmpty() ? "No Scale found; check that it is powered on" : "Choose a Scale");
        }, 5000);
    }
    @Override public void onRequestPermissionsResult(int request, String[] permissions, int[] grants) {
        super.onRequestPermissionsResult(request, permissions, grants);
        if (request != 1) return;
        for (int grant : grants) {
            if (grant != PackageManager.PERMISSION_GRANTED) { status.setText("Permission denied; allow Bluetooth access in app settings"); return; }
        }
        if (grants.length > 0) scan();
    }
    /** Copy scale-mock.json from the sample folder to src/main/assets to try the app without a Scale. */
    private void chooseDemo() {
        try (InputStream input = getAssets().open("scale-mock.json")) {
            ByteArrayOutputStream text = new ByteArrayOutputStream();
            byte[] buffer = new byte[8192];
            for (int read; (read = input.read(buffer)) > 0; ) text.write(buffer, 0, read);
            demoChoice = demoTransport(new JSONObject(text.toString("UTF-8")));
        } catch (IOException error) {
            status.setText("Copy scale-mock.json to src/main/assets for the demo"); return;
        } catch (JSONException error) {
            status.setText("Error: " + error.getMessage()); return;
        }
        devices.clearCheck(); bleChoice = null;
        status.setText("Demo without hardware selected; press Connect"); refresh();
    }
    /** The recorded synthetic replies of scale-mock.json; no measurement or protocol logic. */
    private MockTransport demoTransport(JSONObject script) throws JSONException {
        MockTransport mock = new MockTransport(new HashMap<>());
        JSONObject replies = script.getJSONObject("replies");
        for (Iterator<String> names = replies.keys(); names.hasNext(); ) {
            String name = names.next();
            List<byte[]> chunks = new ArrayList<>();
            chunks.add(bytes(replies.getString(name)));
            if (name.equals("START_CONTINUOUS_MEASUREMENT")) {
                for (int i = 0; i < script.getInt("frame_count"); i++) chunks.add(bytes(script.getString("measurement_hex")));
            }
            mock.reply(core.command(name, 0.0, new byte[0]), chunks.toArray(new byte[0][]));
        }
        return mock;
    }
    private static byte[] bytes(String hex) {
        byte[] data = new byte[hex.length() / 2];
        for (int i = 0; i < data.length; i++) data[i] = (byte) Integer.parseInt(hex.substring(2 * i, 2 * i + 2), 16);
        return data;
    }
    private void connect() {
        if (client != null || busy || (bleChoice == null && demoChoice == null)) return;
        FisicaTransport transport;
        int link;
        // MockTransport declares no link, so connect is always given one.
        if (demoChoice != null) { transport = demoChoice; link = FisicaLink.USB; }
        else { ble.stopScan(); ble.select(bleChoice); transport = ble; link = FisicaLink.BLE; }
        FisicaClient current = new FisicaClient(core, transport, license, runnable -> runOnUiThread(runnable));
        client = current; busy = true; showReport(null); status.setText("Connecting"); refresh();
        current.setOnError(error -> { fail(error, current); return Unit.INSTANCE; });
        current.setOnLinkLost(() -> { fail(new IllegalStateException("The connection to the Scale was lost"), current); return Unit.INSTANCE; });
        current.connect(link, 15000, (event, error) -> {
            if (client != current) return Unit.INSTANCE;
            if (error != null) { fail(error, current); return Unit.INSTANCE; }
            // Measurement packets carry no battery field: read it now; the report shows this reading.
            current.readBattery((percent, batteryError) -> {
                if (client != current) return Unit.INSTANCE;
                connected = true; busy = false;
                String firmware = current.getFirmwareVersion();
                status.setText("Connected (firmware " + (firmware == null ? "unknown" : firmware) + ")"); refresh();
                return Unit.INSTANCE;
            });
            return Unit.INSTANCE;
        });
    }
    private void measure() {
        FisicaClient current = client;
        if (current == null || !connected || busy) return;
        busy = true; showReport(null); status.setText("Measuring for 5 seconds"); refresh();
        current.start(null, (started, error) -> {
            if (client != current) return Unit.INSTANCE;
            if (error != null) { fail(error, current); return Unit.INSTANCE; }
            main.postDelayed(() -> {
                if (client != current) return;
                current.stop((stopped, stopError) -> {
                    if (client != current) return Unit.INSTANCE;
                    if (stopError != null) { fail(stopError, current); return Unit.INSTANCE; }
                    try {
                        // Weight and foot size come from every packet, in basic and full mode alike.
                        SessionReport report = current.report();
                        showReport(report);
                        status.setText(report == null ? NO_RESULT : "Measurement done");
                        busy = false; refresh();
                    } catch (RuntimeException failure) { fail(failure, current); }
                    return Unit.INSTANCE;
                });
            }, 5000);
            return Unit.INSTANCE;
        });
    }
    private void showReport(SessionReport report) {
        if (report == null) { result.setText("Weight: -\nLeft foot: -\nRight foot: -\nFrames: -\nBattery: -"); return; }
        Integer battery = report.getBattery();
        result.setText(String.format("Weight: %.1f kg\nLeft foot: length %.1f cm, width %.1f cm\n"
                + "Right foot: length %.1f cm, width %.1f cm\nFrames: %d\nBattery: %s",
            report.getWeight(), report.getLeftLength(), report.getLeftWidth(), report.getRightLength(), report.getRightWidth(),
            report.getFrameCount(), battery == null ? "not read" : battery + "%"));
    }
    /** One clear line, and the client is always closed. */
    private void fail(Throwable error, FisicaClient failed) {
        if (client == failed) release("Error: " + (error.getMessage() == null ? error : error.getMessage()));
    }
    /** Close the client (the Scale leaves measurement mode); resources are freed even without an answer. */
    private void release(String message) {
        FisicaClient current = client;
        if (current == null) return;
        client = null; connected = false; busy = true; status.setText(message); refresh();
        current.close((event, error) -> { busy = false; refresh(); return Unit.INSTANCE; });
    }
    @Override public void onDestroy() {
        main.removeCallbacksAndMessages(null);
        if (ble != null) ble.stopScan();
        if (client != null) client.close((event, error) -> Unit.INSTANCE);
        if (license != null) license.close();
        super.onDestroy();
    }
}
