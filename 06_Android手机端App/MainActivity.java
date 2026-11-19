package com.bme.ppg;

import android.app.Activity;
import android.app.AlertDialog;
import android.bluetooth.BluetoothAdapter;
import android.bluetooth.BluetoothDevice;
import android.bluetooth.BluetoothSocket;
import android.content.Context;
import android.content.DialogInterface;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.media.AudioManager;
import android.media.ToneGenerator;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.VibrationEffect;
import android.os.Vibrator;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

import java.io.File;
import java.io.FileWriter;
import java.io.InputStream;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Date;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.UUID;

public class MainActivity extends Activity {

    private static final UUID SPP_UUID =
            UUID.fromString("00001101-0000-1000-8000-00805F9B34FB");

    private static final int BG = 0xFF06090F;
    private static final int SURFACE = 0xFF0E141C;
    private static final int STROKE = 0xFF1E2A38;
    private static final int TEXT = 0xFFEAF0F6;
    private static final int DIM = 0xFF7D8B9C;
    private static final int CYAN = 0xFF22D3EE;
    private static final int RED = 0xFFFB7185;
    private static final int GREEN = 0xFF34D399;
    private static final int AMBER = 0xFFFBBF24;

    private static final int MAX_POINTS = 640;
    private static final int REQ_BT = 1001;
    private static final float SAMPLE_RATE = 50f;
    private static final int MIN_PP = 1200;

    private final Object lock = new Object();
    private final ArrayList<Integer> waveIr = new ArrayList<>();
    private final ArrayList<Integer> waveRed = new ArrayList<>();

    private Integer spo2 = null;
    private Integer pr = null;
    private double pi = 0;
    private int pitch = 0;
    private int roll = 0;
    private int stateVal = 0;
    private int frameCount = 0;
    private boolean connected = false;
    private boolean alarmActive = false;
    private long alarmMutedUntil = 0;
    private long localAlarmUntil = 0;

    private BluetoothAdapter adapter;
    private BluetoothSocket socket;
    private volatile boolean running = false;
    private Thread reader;

    private BluetoothDevice lastDevice;
    private boolean autoReconnect = true;
    private int reconnectAttempts = 0;

    private boolean recording = false;
    private FileWriter csvWriter = null;
    private String csvPath = "";

    private BluetoothAdapter reservedAdapter;
    private TextView tvLink;
    private TextView tvSpo2;
    private TextView tvPr;
    private TextView tvPi;
    private TextView tvState;
    private TextView tvHint;
    private TextView tvAtt;
    private TextView tvFrames;
    private TextView tvWaveTitle;
    private Button btnDisconnect;
    private Button btnFall;
    private Button btnRecord;
    private QualityView qualityView;
    private WaveView waveView;
    private FrameLayout rootFrame;
    private View alarmOverlay;

    private final Handler ui = new Handler(Looper.getMainLooper());

    private final Runnable ticker = new Runnable() {
        @Override
        public void run() {
            if (waveView != null) waveView.invalidate();
            ui.postDelayed(this, 50);
        }
    };

    private ToneGenerator tone = null;
    private Vibrator vibrator = null;

    // ================= 生命周期 =================

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        buildUi();
        adapter = BluetoothAdapter.getDefaultAdapter();
        reservedAdapter = adapter;
        if (adapter == null) {
            setStatus("本机不支持蓝牙", RED);
            return;
        }
        requestBtPermissionIfNeeded();
        ui.post(ticker);
        showGuideIfFirstRun();
    }

    @Override
    protected void onDestroy() {
        super.onDestroy();
        ui.removeCallbacks(ticker);
        stopAlarm();
        stopRecord(false);
        running = false;
        closeSocket();
    }

    private void showGuideIfFirstRun() {
        SharedPreferences sp = getSharedPreferences("cfg", MODE_PRIVATE);
        if (sp.getBoolean("guided", false)) return;
        new AlertDialog.Builder(this)
                .setTitle("开始使用前先配对")
                .setMessage("1. 板子上电，JDY-31 指示灯闪烁\n"
                        + "2. 手机 设置 → 蓝牙 → 搜索新设备\n"
                        + "3. 选择 JDY-31 配对，PIN 输入 1234\n"
                        + "4. 回到本 App 点右上角「未连接」选择设备\n\n"
                        + "JDY-31 同一时刻只能连一台主设备，电脑端上位机连着时要先断开。")
                .setPositiveButton("知道了", new DialogInterface.OnClickListener() {
                    @Override
                    public void onClick(DialogInterface d, int w) {
                    }
                })
                .setNegativeButton("去打开蓝牙设置", new DialogInterface.OnClickListener() {
                    @Override
                    public void onClick(DialogInterface d, int w) {
                        try {
                            startActivity(new android.content.Intent(
                                    android.provider.Settings.ACTION_BLUETOOTH_SETTINGS));
                        } catch (Exception ignored) {
                        }
                    }
                })
                .show();
        sp.edit().putBoolean("guided", true).apply();
    }

    // ================= 基础绘制工具 =================

    private int dp(int v) {
        return (int) TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v,
                getResources().getDisplayMetrics());
    }

    private GradientDrawable round(int color, int strokeColor, int radiusDp) {
        GradientDrawable g = new GradientDrawable();
        g.setColor(color);
        g.setCornerRadius(dp(radiusDp));
        if (strokeColor != 0) g.setStroke(dp(1), strokeColor);
        return g;
    }

    private TextView makeLabel(String text, int sizeSp, int color) {
        TextView t = new TextView(this);
        t.setText(text);
        t.setTextSize(TypedValue.COMPLEX_UNIT_SP, sizeSp);
        t.setTextColor(color);
        return t;
    }

    private Button makeBtn(String text, int textColor, boolean solid) {
        Button b = new Button(this);
        b.setText(text);
        b.setTextColor(textColor);
        b.setTextSize(TypedValue.COMPLEX_UNIT_SP, 13);
        b.setAllCaps(false);
        b.setTypeface(Typeface.DEFAULT_BOLD);
        GradientDrawable g = new GradientDrawable();
        g.setCornerRadius(dp(14));
        if (solid) {
            g.setColor(textColor);
            b.setTextColor(BG);
        } else {
            g.setColor(0xFF121A24);
            g.setStroke(dp(1), textColor);
        }
        b.setBackground(g);
        b.setPadding(dp(8), dp(10), dp(8), dp(10));
        if (Build.VERSION.SDK_INT >= 21) b.setStateListAnimator(null);
        return b;
    }

    private TextView makePill(String text, int color) {
        TextView t = new TextView(this);
        t.setText(text);
        t.setTextSize(TypedValue.COMPLEX_UNIT_SP, 12);
        t.setTextColor(color);
        t.setGravity(Gravity.CENTER);
        t.setPadding(dp(12), dp(6), dp(12), dp(6));
        t.setBackground(round(0xFF121A24, color, 20));
        return t;
    }

    // ================= 界面搭建 =================

    private void buildUi() {
        rootFrame = new FrameLayout(this);
        rootFrame.setBackgroundColor(BG);

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(dp(16), dp(14), dp(16), dp(14));

        // 顶部栏
        LinearLayout header = new LinearLayout(this);
        header.setOrientation(LinearLayout.HORIZONTAL);
        header.setGravity(Gravity.CENTER_VERTICAL);

        LinearLayout titleBox = new LinearLayout(this);
        titleBox.setOrientation(LinearLayout.VERTICAL);
        TextView title = makeLabel("血氧监测", 22, TEXT);
        title.setTypeface(Typeface.DEFAULT_BOLD);
        TextView subtitle = makeLabel("MAX30102 · 蓝牙实时监护", 11, DIM);
        titleBox.addView(title);
        titleBox.addView(subtitle);
        header.addView(titleBox, new LinearLayout.LayoutParams(0,
                LinearLayout.LayoutParams.WRAP_CONTENT, 1f));

        tvLink = makePill("未连接", DIM);
        tvLink.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                showDevicePicker();
            }
        });
        header.addView(tvLink);
        root.addView(header);

        // 指标卡片区
        LinearLayout cards = new LinearLayout(this);
        cards.setOrientation(LinearLayout.HORIZONTAL);
        LinearLayout.LayoutParams clp = new LinearLayout.LayoutParams(0,
                LinearLayout.LayoutParams.WRAP_CONTENT, 1f);
        clp.setMargins(0, dp(14), dp(8), 0);

        LinearLayout spo2Card = makeCard("血氧饱和度", "%", CYAN);
        tvSpo2 = (TextView) spo2Card.getTag();
        spo2Card.setLayoutParams(clp);
        cards.addView(spo2Card);

        LinearLayout prCard = makeCard("脉率", "bpm", TEXT);
        tvPr = (TextView) prCard.getTag();
        LinearLayout.LayoutParams clp2 = new LinearLayout.LayoutParams(0,
                LinearLayout.LayoutParams.WRAP_CONTENT, 1f);
        clp2.setMargins(0, dp(14), 8, 0);
        prCard.setLayoutParams(clp2);
        cards.addView(prCard);

        LinearLayout piCard = makeCard("灌注指数", "%", GREEN);
        tvPi = (TextView) piCard.getTag();
        LinearLayout.LayoutParams clp3 = new LinearLayout.LayoutParams(0,
                LinearLayout.LayoutParams.WRAP_CONTENT, 1f);
        clp3.setMargins(0, dp(14), 0, 0);
        piCard.setLayoutParams(clp3);
        cards.addView(piCard);

        root.addView(cards);

        // 状态行
        LinearLayout statusRow = new LinearLayout(this);
        statusRow.setOrientation(LinearLayout.HORIZONTAL);
        statusRow.setGravity(Gravity.CENTER_VERTICAL);
        statusRow.setPadding(0, dp(12), 0, dp(8));

        tvState = makePill("空闲", DIM);
        statusRow.addView(tvState);

        qualityView = new QualityView(this);
        LinearLayout.LayoutParams qlp = new LinearLayout.LayoutParams(dp(56), dp(14));
        qlp.setMargins(dp(10), 0, dp(8), 0);
        qualityView.setLayoutParams(qlp);
        statusRow.addView(qualityView);

        tvHint = makeLabel("", 11, DIM);
        tvHint.setGravity(Gravity.CENTER_VERTICAL);
        statusRow.addView(tvHint, new LinearLayout.LayoutParams(0,
                LinearLayout.LayoutParams.WRAP_CONTENT, 1f));

        tvFrames = makeLabel("0 帧", 11, DIM);
        statusRow.addView(tvFrames);
        root.addView(statusRow);

        // 波形卡片
        LinearLayout waveCard = new LinearLayout(this);
        waveCard.setOrientation(LinearLayout.VERTICAL);
        waveCard.setBackground(round(SURFACE, STROKE, 18));
        waveCard.setPadding(dp(12), dp(10), dp(12), dp(10));

        LinearLayout waveHead = new LinearLayout(this);
        waveHead.setOrientation(LinearLayout.HORIZONTAL);
        tvWaveTitle = makeLabel("实时波形 · 50 Hz", 11, DIM);
        waveHead.addView(tvWaveTitle, new LinearLayout.LayoutParams(0,
                LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
        TextView legend = makeLabel("IR ●    RED ●    PULSE ●", 10, DIM);
        waveHead.addView(legend);
        waveCard.addView(waveHead);

        waveView = new WaveView(this, this);
        LinearLayout.LayoutParams wlp = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f);
        wlp.setMargins(0, dp(8), 0, 0);
        waveCard.addView(waveView, wlp);

        LinearLayout.LayoutParams cardLp = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f);
        cardLp.setMargins(0, dp(4), 0, dp(10));
        root.addView(waveCard, cardLp);

        // 姿态行
        LinearLayout attRow = new LinearLayout(this);
        attRow.setOrientation(LinearLayout.HORIZONTAL);
        attRow.setPadding(0, 0, 0, dp(10));
        tvAtt = makeLabel("姿态 --", 12, DIM);
        attRow.addView(tvAtt, new LinearLayout.LayoutParams(0,
                LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
        TextView rate = makeLabel("", 12, DIM);
        rate.setText("SAMPLE 50Hz");
        attRow.addView(rate);
        root.addView(attRow);

        // 按钮行
        LinearLayout btns = new LinearLayout(this);
        btns.setOrientation(LinearLayout.HORIZONTAL);
        btnDisconnect = makeBtn("断开", DIM, false);
        btnDisconnect.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                disconnect();
                setStatus("未连接", DIM);
            }
        });
        btnFall = makeBtn("模拟跌倒", AMBER, false);
        btnFall.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                sendFall();
            }
        });
        btnRecord = makeBtn("开始记录", CYAN, false);
        btnRecord.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                toggleRecord();
            }
        });
        LinearLayout.LayoutParams blp = new LinearLayout.LayoutParams(0,
                LinearLayout.LayoutParams.WRAP_CONTENT, 1f);
        blp.setMargins(0, 0, dp(8), 0);
        btnDisconnect.setLayoutParams(blp);
        LinearLayout.LayoutParams blp2 = new LinearLayout.LayoutParams(0,
                LinearLayout.LayoutParams.WRAP_CONTENT, 1f);
        blp2.setMargins(0, 0, dp(8), 0);
        btnFall.setLayoutParams(blp2);
        btnRecord.setLayoutParams(new LinearLayout.LayoutParams(0,
                LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
        btns.addView(btnDisconnect);
        btns.addView(btnFall);
        btns.addView(btnRecord);
        root.addView(btns);

        rootFrame.addView(root, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.MATCH_PARENT));

        // 跌倒遮罩
        alarmOverlay = buildAlarmOverlay();
        rootFrame.addView(alarmOverlay, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.MATCH_PARENT));

        setContentView(rootFrame);
        updateMetrics();
    }

    private LinearLayout makeCard(String title, String unit, int valueColor) {
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setBackground(round(SURFACE, STROKE, 18));
        box.setPadding(dp(12), dp(12), dp(12), dp(12));

        TextView label = makeLabel(title, 11, DIM);
        box.addView(label);

        TextView value = new TextView(this);
        value.setText("--");
        value.setTextColor(valueColor);
        value.setTextSize(TypedValue.COMPLEX_UNIT_SP, 34);
        value.setTypeface(Typeface.DEFAULT_BOLD);
        value.setIncludeFontPadding(false);
        LinearLayout.LayoutParams vlp = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT,
                LinearLayout.LayoutParams.WRAP_CONTENT);
        vlp.setMargins(0, dp(6), 0, 0);
        box.addView(value, vlp);

        TextView u = makeLabel(unit, 10, DIM);
        box.addView(u);

        box.setTag(value);
        return box;
    }

    private View buildAlarmOverlay() {
        FrameLayout overlay = new FrameLayout(this);
        GradientDrawable bg = new GradientDrawable(
                GradientDrawable.Orientation.TOP_BOTTOM,
                new int[]{0xF2B0121F, 0xF27F0D18});
        overlay.setBackground(bg);
        overlay.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                stopAlarm();
                alarmMutedUntil = System.currentTimeMillis() + 8000;
            }
        });

        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setGravity(Gravity.CENTER);

        TextView big = makeLabel("检测到跌倒", 34, Color.WHITE);
        big.setTypeface(Typeface.DEFAULT_BOLD);
        box.addView(big);

        TextView desc = makeLabel("请确认被测者状态", 14, 0xFFFFD9DE);
        desc.setGravity(Gravity.CENTER);
        LinearLayout.LayoutParams dlp = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT,
                LinearLayout.LayoutParams.WRAP_CONTENT);
        dlp.setMargins(0, dp(8), 0, 0);
        box.addView(desc, dlp);

        TextView tip = makeLabel("点击屏幕解除", 12, 0xFFFFB0B8);
        LinearLayout.LayoutParams tlp = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT,
                LinearLayout.LayoutParams.WRAP_CONTENT);
        tlp.setMargins(0, dp(24), 0, 0);
        box.addView(tip, tlp);

        FrameLayout.LayoutParams blp = new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.WRAP_CONTENT,
                FrameLayout.LayoutParams.WRAP_CONTENT);
        blp.gravity = Gravity.CENTER;
        overlay.addView(box, blp);

        overlay.setVisibility(View.GONE);
        overlay.setClickable(true);
        return overlay;
    }

    private void setStatus(final String text, final int color) {
        ui.post(new Runnable() {
            @Override
            public void run() {
                if (tvLink == null) return;
                tvLink.setText(text);
                tvLink.setTextColor(color);
                tvLink.setBackground(round(0xFF121A24, color, 20));
            }
        });
    }

    // ================= 权限 =================

    private boolean hasBtPermission() {
        if (Build.VERSION.SDK_INT >= 31) {
            return checkSelfPermission("android.permission.BLUETOOTH_CONNECT")
                    == PackageManager.PERMISSION_GRANTED
                    && checkSelfPermission("android.permission.BLUETOOTH_SCAN")
                    == PackageManager.PERMISSION_GRANTED;
        }
        return true;
    }

    private void requestBtPermissionIfNeeded() {
        if (Build.VERSION.SDK_INT >= 31 && !hasBtPermission()) {
            requestPermissions(new String[]{
                    "android.permission.BLUETOOTH_CONNECT",
                    "android.permission.BLUETOOTH_SCAN"}, REQ_BT);
        } else {
            setStatus(adapter != null && adapter.isEnabled() ? "未连接" : "蓝牙未开启",
                    adapter != null && adapter.isEnabled() ? DIM : AMBER);
        }
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] perms, int[] results) {
        super.onRequestPermissionsResult(requestCode, perms, results);
        if (requestCode == REQ_BT) {
            setStatus(hasBtPermission() ? "未连接" : "权限被拒绝", hasBtPermission() ? DIM : RED);
        }
    }

    // ================= 蓝牙 =================

    private void showDevicePicker() {
        if (adapter == null) return;
        if (!adapter.isEnabled()) {
            setStatus("蓝牙未开启", AMBER);
            return;
        }
        if (Build.VERSION.SDK_INT >= 31 && !hasBtPermission()) {
            requestBtPermissionIfNeeded();
            return;
        }
        Set<BluetoothDevice> bonded;
        try {
            bonded = adapter.getBondedDevices();
        } catch (SecurityException e) {
            setStatus("没有蓝牙权限", RED);
            return;
        }
        if (bonded == null || bonded.isEmpty()) {
            setStatus("无已配对设备", AMBER);
            Toast.makeText(this, "请先在系统蓝牙里配对 JDY-31（PIN 通常 1234）",
                    Toast.LENGTH_LONG).show();
            return;
        }
        final List<BluetoothDevice> list = new ArrayList<>(bonded);
        String[] items = new String[list.size()];
        for (int i = 0; i < list.size(); i++) {
            BluetoothDevice d = list.get(i);
            String n = null;
            try {
                n = d.getName();
            } catch (SecurityException ignored) {
            }
            if (n == null || n.length() == 0) n = "未知设备";
            items[i] = n + "  (" + d.getAddress() + ")";
        }
        new AlertDialog.Builder(this)
                .setTitle("选择设备")
                .setItems(items, new DialogInterface.OnClickListener() {
                    @Override
                    public void onClick(DialogInterface dialog, int which) {
                        connectTo(list.get(which));
                    }
                })
                .setNegativeButton("取消", null)
                .show();
    }

    private void connectTo(final BluetoothDevice device) {
        if (!hasBtPermission()) {
            requestBtPermissionIfNeeded();
            return;
        }
        closeSocket();
        running = true;
        lastDevice = device;
        String name = null;
        try {
            name = device.getName();
        } catch (SecurityException ignored) {
        }
        final String label = (name == null || name.length() == 0) ? device.getAddress() : name;
        setStatus("连接中...", AMBER);

        new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    try {
                        adapter.cancelDiscovery();
                    } catch (Exception ignored) {
                    }
                    BluetoothSocket s = device.createRfcommSocketToServiceRecord(SPP_UUID);
                    socket = s;
                    s.connect();
                    connected = true;
                    reconnectAttempts = 0;
                    setStatus("已连接 · " + label, GREEN);
                    reader = new Thread(new Runnable() {
                        @Override
                        public void run() {
                            readLoop();
                        }
                    }, "spp-reader");
                    reader.start();
                } catch (Exception e) {
                    connected = false;
                    setStatus("连接失败", RED);
                    closeSocket();
                }
            }
        }, "spp-connect").start();
    }

    private void readLoop() {
        try {
            InputStream in = socket.getInputStream();
            byte[] tmp = new byte[256];
            while (running) {
                int r = in.read(tmp);
                if (r <= 0) continue;
                synchronized (lock) {
                    pushBytes(tmp, r);
                    parseBuffer();
                }
            }
        } catch (Exception ignored) {
        } finally {
            connected = false;
            if (running && autoReconnect && lastDevice != null) {
                scheduleReconnect();
            } else {
                setStatus("连接断开", AMBER);
            }
        }
    }

    private void scheduleReconnect() {
        if (!running) return;
        if (reconnectAttempts >= 5) {
            setStatus("重连失败", RED);
            return;
        }
        reconnectAttempts++;
        setStatus("重连中 " + reconnectAttempts + "/5", AMBER);
        ui.postDelayed(new Runnable() {
            @Override
            public void run() {
                if (running && lastDevice != null) connectTo(lastDevice);
            }
        }, 3000);
    }

    private void closeSocket() {
        BluetoothSocket s = socket;
        socket = null;
        try {
            if (s != null) s.close();
        } catch (Exception ignored) {
        }
    }

    private void disconnect() {
        running = false;
        reconnectAttempts = 0;
        closeSocket();
        if (reader != null) {
            try {
                reader.join(800);
            } catch (InterruptedException ignored) {
            }
            reader = null;
        }
        connected = false;
        synchronized (lock) {
            spo2 = null;
            pr = null;
            pi = 0;
            frameCount = 0;
            bufLen = 0;
        }
        stopAlarm();
        updateMetrics();
    }

    private void sendFall() {
        if (!connected || socket == null) {
            setStatus("未连接", AMBER);
            return;
        }
        // 本机立刻给出报警反馈，不等固件回传，保证演示时一定有效果
        alarmMutedUntil = 0;
        localAlarmUntil = System.currentTimeMillis() + 4000;
        startAlarm();
        updateMetrics();
        new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    socket.getOutputStream().write(new byte[]{'F', 'F', 'F', 'F'});
                    socket.getOutputStream().flush();
                } catch (Exception ignored) {
                }
            }
        }).start();
    }

    // ================= 协议 =================

    private final byte[] buf = new byte[8192];
    private int bufLen = 0;

    private void pushBytes(byte[] chunk, int len) {
        if (bufLen + len > buf.length) {
            int keep = Math.min(bufLen, buf.length / 2);
            if (keep > 0) System.arraycopy(buf, bufLen - keep, buf, 0, keep);
            bufLen = keep;
        }
        if (bufLen + len > buf.length) return;
        System.arraycopy(chunk, 0, buf, bufLen, len);
        bufLen += len;
    }

    private void parseBuffer() {
        while (bufLen >= 6) {
            int start = -1;
            for (int i = 0; i + 1 < bufLen; i++) {
                if ((buf[i] & 0xFF) == 0xAA && (buf[i + 1] & 0xFF) == 0x55) {
                    start = i;
                    break;
                }
            }
            if (start < 0) {
                bufLen = 0;
                return;
            }
            if (start > 0) {
                System.arraycopy(buf, start, buf, 0, bufLen - start);
                bufLen -= start;
            }
            if (bufLen < 5) return;

            int type = buf[2] & 0xFF;
            int len = buf[3] & 0xFF;
            int need = 4 + len + 1;
            if (need > buf.length) {
                bufLen = 0;
                return;
            }
            if (bufLen < need) return;

            int sum = 0;
            for (int i = 0; i < 4 + len; i++) sum += buf[i] & 0xFF;
            if ((sum & 0xFF) != (buf[4 + len] & 0xFF)) {
                System.arraycopy(buf, 1, buf, 0, bufLen - 1);
                bufLen--;
                continue;
            }

            int[] pl = new int[len];
            for (int i = 0; i < len; i++) pl[i] = buf[4 + i] & 0xFF;
            handleFrame(type, pl);

            System.arraycopy(buf, need, buf, 0, bufLen - need);
            bufLen -= need;
        }
    }

    private void handleFrame(int type, int[] pl) {
        if (type == 0x01 && pl.length >= 6) {
            spo2 = pl[0] == 0 ? null : Integer.valueOf(pl[0]);
            pr = pl[1] == 0 ? null : Integer.valueOf(pl[1]);
            pi = pl[2] / 10.0;
            pitch = pl[3] - 90;
            roll = pl[4] - 90;
            stateVal = pl[5];
            frameCount++;
            writeCsv();
            ui.post(new Runnable() {
                @Override
                public void run() {
                    updateMetrics();
                }
            });
        } else if (type == 0x03) {
            if (pl.length >= 12) {
                addWave(pl[0], pl[1], pl[2], pl[3], pl[4], pl[5]);
                addWave(pl[6], pl[7], pl[8], pl[9], pl[10], pl[11]);
            } else if (pl.length >= 6) {
                addWave(pl[0], pl[1], pl[2], 0, 0, 0);
                addWave(pl[3], pl[4], pl[5], 0, 0, 0);
            }
        } else if (type == 0x02) {
            stateVal = 4;
            ui.post(new Runnable() {
                @Override
                public void run() {
                    updateMetrics();
                }
            });
        }
    }

    private void addWave(int ir0, int ir1, int ir2, int red0, int red1, int red2) {
        waveIr.add(Integer.valueOf((ir0 << 16) | (ir1 << 8) | ir2));
        waveRed.add(Integer.valueOf((red0 << 16) | (red1 << 8) | red2));
        while (waveIr.size() > MAX_POINTS) waveIr.remove(0);
        while (waveRed.size() > MAX_POINTS) waveRed.remove(0);
    }

    private String stateName(int s) {
        if (s == 0) return "空闲";
        if (s == 1) return "测量中";
        if (s == 2) return "信号弱";
        if (s == 4) return "跌倒报警";
        return "状态 " + s;
    }

    // ================= UI 刷新 =================

    private int signalQuality(int[] ir) {
        if (ir.length < 20) return 0;
        int lo = Integer.MAX_VALUE, hi = Integer.MIN_VALUE;
        int n = Math.min(ir.length, 320);
        for (int i = 0; i < n; i++) {
            int v = ir[ir.length - n + i];
            if (v < lo) lo = v;
            if (v > hi) hi = v;
        }
        int pp = hi - lo;
        if (pp < 1200) return 0;
        if (pp < 5000) return 1;
        if (pp < 20000) return 2;
        return pi >= 1.0 ? 4 : 3;
    }

    private void updateMetrics() {
        Integer s2, p2;
        double pi2;
        int pitch2, roll2, st2, fc2;
        int[] irSnap;
        synchronized (lock) {
            s2 = spo2;
            p2 = pr;
            pi2 = pi;
            pitch2 = pitch;
            roll2 = roll;
            st2 = stateVal;
            fc2 = frameCount;
            int n = waveIr.size();
            irSnap = new int[n];
            for (int i = 0; i < n; i++) irSnap[i] = waveIr.get(i).intValue();
        }
        if (tvSpo2 == null) return;

        tvSpo2.setText(s2 == null ? "--" : String.valueOf(s2.intValue()));
        tvPr.setText(p2 == null ? "--" : String.valueOf(p2.intValue()));
        tvPi.setText(pi2 <= 0 ? "--" : String.format(Locale.US, "%.1f", pi2));

        tvState.setText(stateName(st2));
        int stColor = DIM;
        if (st2 == 1) stColor = CYAN;
        if (st2 == 2) stColor = AMBER;
        if (st2 == 4) stColor = RED;
        tvState.setTextColor(stColor);
        tvState.setBackground(round(0xFF121A24, stColor, 20));

        int q = signalQuality(irSnap);
        qualityView.setQuality(q);

        if (st2 == 2 || q <= 1) {
            tvHint.setText("指腹覆盖不足");
        } else if (!connected) {
            tvHint.setText("蓝牙未连接");
        } else {
            tvHint.setText("");
        }

        tvAtt.setText(String.format(Locale.US, "姿态 %+d° / %+d°", pitch2, roll2));
        tvFrames.setText(fc2 + " 帧");

        if (st2 == 4) {
            if (!alarmActive && System.currentTimeMillis() >= alarmMutedUntil) startAlarm();
        } else {
            alarmMutedUntil = 0;
            if (alarmActive && System.currentTimeMillis() > localAlarmUntil) stopAlarm();
        }
    }

    // ================= 跌倒报警 =================

    private void startAlarm() {
        alarmActive = true;
        if (alarmOverlay != null) {
            alarmOverlay.setVisibility(View.VISIBLE);
            alarmOverlay.bringToFront();
        }
        try {
            if (vibrator == null) {
                vibrator = (Vibrator) getSystemService(Context.VIBRATOR_SERVICE);
            }
            if (vibrator != null && vibrator.hasVibrator()) {
                long[] pattern = new long[]{0, 400, 200, 400, 200, 800};
                if (Build.VERSION.SDK_INT >= 26) {
                    vibrator.vibrate(VibrationEffect.createWaveform(pattern, -1));
                } else {
                    vibrator.vibrate(pattern, -1);
                }
            }
        } catch (Exception ignored) {
        }
        try {
            if (tone == null) {
                tone = new ToneGenerator(AudioManager.STREAM_ALARM, 90);
            }
            tone.startTone(ToneGenerator.TONE_CDMA_ALERT_CALL_GUARD, 3000);
        } catch (Exception ignored) {
        }
    }

    private void stopAlarm() {
        alarmActive = false;
        if (alarmOverlay != null) alarmOverlay.setVisibility(View.GONE);
        try {
            if (vibrator != null) vibrator.cancel();
        } catch (Exception ignored) {
        }
        try {
            if (tone != null) tone.stopTone();
        } catch (Exception ignored) {
        }
    }

    // ================= CSV 记录 =================

    private void toggleRecord() {
        if (recording) {
            stopRecord(true);
            return;
        }
        try {
            File dir = getExternalFilesDir(null);
            if (dir == null) dir = getFilesDir();
            if (!dir.exists()) dir.mkdirs();
            String name = "ppg_" + new SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US)
                    .format(new Date()) + ".csv";
            File f = new File(dir, name);
            csvWriter = new FileWriter(f, true);
            csvWriter.write("timestamp_ms,spo2,pr,pi,pitch,roll,state\n");
            csvWriter.flush();
            csvPath = f.getAbsolutePath();
            recording = true;
            btnRecord.setText("停止记录");
            btnRecord.setTextColor(RED);
            btnRecord.setBackground(roundOutline(RED));
            Toast.makeText(this, "记录中：" + csvPath, Toast.LENGTH_LONG).show();
        } catch (Exception e) {
            Toast.makeText(this, "无法创建文件：" + e.getMessage(), Toast.LENGTH_LONG).show();
        }
    }

    private GradientDrawable roundOutline(int color) {
        GradientDrawable g = new GradientDrawable();
        g.setColor(0xFF121A24);
        g.setCornerRadius(dp(14));
        g.setStroke(dp(1), color);
        return g;
    }

    private void stopRecord(boolean toast) {
        recording = false;
        if (csvWriter != null) {
            try {
                csvWriter.flush();
                csvWriter.close();
            } catch (Exception ignored) {
            }
            csvWriter = null;
        }
        if (btnRecord != null) {
            btnRecord.setText("开始记录");
            btnRecord.setTextColor(CYAN);
            btnRecord.setBackground(roundOutline(CYAN));
        }
        if (toast && csvPath.length() > 0) {
            Toast.makeText(this, "已保存：\n" + csvPath, Toast.LENGTH_LONG).show();
        }
    }

    private void writeCsv() {
        if (!recording || csvWriter == null) return;
        try {
            csvWriter.write(System.currentTimeMillis() + ","
                    + (spo2 == null ? "" : spo2) + ","
                    + (pr == null ? "" : pr) + ","
                    + String.format(Locale.US, "%.1f", pi) + ","
                    + pitch + "," + roll + "," + stateVal + "\n");
            csvWriter.flush();
        } catch (Exception ignored) {
        }
    }

    // ================= 信号质量指示 =================

    public static class QualityView extends View {
        private final Paint p = new Paint(Paint.ANTI_ALIAS_FLAG);
        private int q = 0;

        QualityView(Context c) {
            super(c);
        }

        void setQuality(int v) {
            if (v != q) {
                q = v;
                invalidate();
            }
        }

        @Override
        protected void onDraw(Canvas canvas) {
            int h = getHeight();
            int w = getWidth();
            int gap = w / 14;
            int bw = (w - gap * 3) / 4;
            int colors[] = new int[]{DIM, AMBER, AMBER, GREEN, CYAN};
            int c = colors[Math.min(q, 4)];
            for (int i = 0; i < 4; i++) {
                int left = i * (bw + gap);
                if (i < q) {
                    p.setColor(c);
                } else {
                    p.setColor(0xFF1E2A38);
                }
                int bh = h - (3 - i) * dp(2);
                p.setStyle(Paint.Style.FILL);
                canvas.drawRoundRect(left, h - bh, left + bw, h, dp(3), dp(3), p);
            }
        }

        private int dp(int v) {
            return (int) TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v,
                    getResources().getDisplayMetrics());
        }
    }

    // ================= 波形 =================

    public static class WaveView extends View {
        private final MainActivity act;
        private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
        private final Paint text = new Paint(Paint.ANTI_ALIAS_FLAG);
        private float ts = 12f;

        WaveView(Context c, MainActivity a) {
            super(c);
            act = a;
            ts = TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_SP, 10,
                    c.getResources().getDisplayMetrics());
            text.setTextSize(ts);
            text.setAntiAlias(true);
        }

        @Override
        protected void onDraw(Canvas canvas) {
            int W = getWidth();
            int H = getHeight();
            if (W < 10 || H < 40) return;
            canvas.drawColor(SURFACE);

            int[] ir, red;
            synchronized (act.lock) {
                int n = act.waveIr.size();
                ir = new int[n];
                red = new int[n];
                for (int i = 0; i < n; i++) {
                    ir[i] = act.waveIr.get(i).intValue();
                    red[i] = act.waveRed.get(i).intValue();
                }
            }

            int per = H / 3;

            // 竖向网格
            paint.setColor(0x14FFFFFF);
            paint.setStrokeWidth(1f);
            for (int i = 1; i < 8; i++) {
                float x = (float) i / 8 * W;
                canvas.drawLine(x, 0, x, H, paint);
            }
            paint.setColor(0x33FFFFFF);
            for (int k = 0; k <= 3; k++) {
                canvas.drawLine(0, per * k, W, per * k, paint);
            }

            int show = Math.min(ir.length, 320);
            float[] a = new float[show];
            float[] b = new float[show];
            for (int i = 0; i < show; i++) {
                a[i] = ir[ir.length - show + i];
                b[i] = red[red.length - show + i];
            }

            float top1 = 3f, bot1 = per - 3f;
            float ppIr = show > 2 ? peakToPeak(a) : 0f;
            float ppRed = show > 2 ? peakToPeak(b) : 0f;
            if (ppIr >= 200f) {
                stroke(canvas, normalize(smooth(a, 3)), W, top1, bot1, CYAN, 2.4f);
            }
            if (ppRed >= 200f) {
                stroke(canvas, normalize(smooth(b, 3)), W, per + 3f, 2 * per - 3f, RED, 1.8f);
            }

            float lh = ts * 1.4f;
            text.setColor(CYAN);
            canvas.drawText("IR", 8f, lh, text);
            if (ppIr < 200f) {
                text.setColor(DIM);
                canvas.drawText("无信号", 8f + text.measureText("IR") + 8f, lh, text);
            }
            text.setColor(RED);
            canvas.drawText("RED", 8f, per + lh, text);
            if (ppRed < 200f) {
                text.setColor(DIM);
                canvas.drawText("无信号", 8f + text.measureText("RED") + 8f, per + lh, text);
            }

            float[] filt = bpFilter(a);
            float pp = peakToPeak(filt);
            int bpm = 0;
            if (show > 40) bpm = act.drawPulse(canvas, filt, W, 2 * per + 3f, H - 3f);

            text.setColor(GREEN);
            canvas.drawText("PULSE", 8f, 2 * per + lh, text);
            float tx = 8f + text.measureText("PULSE") + 10f;
            if (show < 40) {
                text.setColor(DIM);
                canvas.drawText("等待数据", tx, 2 * per + lh, text);
            } else if (pp < MIN_PP) {
                text.setColor(AMBER);
                canvas.drawText("信号弱", tx, 2 * per + lh, text);
            } else if (bpm > 0) {
                text.setColor(GREEN);
                canvas.drawText(bpm + " bpm", tx, 2 * per + lh, text);
            }

            // 右侧实时值
            synchronized (act.lock) {
                text.setColor(0x99FFFFFF);
                if (act.spo2 != null) {
                    canvas.drawText("SpO2 " + act.spo2 + "%", W - text.measureText("SpO2 100%") - 8f,
                            2 * per + lh, text);
                }
            }
        }

        private void stroke(Canvas c, float[] norm, int W, float top, float bot,
                            int color, float width) {
            if (norm.length < 2) return;
            paint.setColor(color);
            paint.setStrokeWidth(width);
            paint.setStyle(Paint.Style.STROKE);
            float x0 = 0f;
            float y0 = bot - norm[0] * (bot - top);
            for (int i = 1; i < norm.length; i++) {
                float x1 = (float) i / (norm.length - 1) * W;
                float y1 = bot - norm[i] * (bot - top);
                c.drawLine(x0, y0, x1, y1, paint);
                x0 = x1;
                y0 = y1;
            }
        }

        static float[] smooth(float[] v, int win) {
            if (v.length < win) return v;
            float[] out = new float[v.length];
            int half = win / 2;
            for (int i = 0; i < v.length; i++) {
                float s = 0;
                int c = 0;
                for (int k = -half; k <= half; k++) {
                    int j = i + k;
                    if (j >= 0 && j < v.length) {
                        s += v[j];
                        c++;
                    }
                }
                out[i] = s / c;
            }
            return out;
        }

        static float[] normalize(float[] v) {
            if (v.length == 0) return v;
            float lo = Float.MAX_VALUE, hi = -Float.MAX_VALUE;
            for (float x : v) {
                if (x < lo) lo = x;
                if (x > hi) hi = x;
            }
            if (hi - lo < 1f) hi = lo + 1f;
            float pad = (hi - lo) * 0.12f;
            float bot = lo - pad, top = hi + pad;
            float[] out = new float[v.length];
            for (int i = 0; i < v.length; i++) out[i] = (v[i] - bot) / (top - bot);
            return out;
        }

        static float[] bpFilter(float[] arr) {
            int n = arr.length;
            if (n == 0) return new float[0];
            float[] out = new float[n];
            int win = 40;
            float sum = 0;
            for (int i = 0; i < n; i++) {
                sum += arr[i];
                if (i >= win) sum -= arr[i - win];
                float mean = sum / Math.min(i + 1, win);
                out[i] = arr[i] - mean;
            }
            float[] sm = new float[n];
            for (int i = 0; i < n; i++) {
                float s = 0;
                int c = 0;
                for (int k = -2; k <= 2; k++) {
                    int j = i + k;
                    if (j >= 0 && j < n) {
                        s += out[j];
                        c++;
                    }
                }
                sm[i] = s / c;
            }
            return sm;
        }

        static float peakToPeak(float[] v) {
            if (v.length == 0) return 0f;
            float lo = Float.MAX_VALUE, hi = -Float.MAX_VALUE;
            for (float x : v) {
                if (x < lo) lo = x;
                if (x > hi) hi = x;
            }
            return hi - lo;
        }
    }

    // ================= 脉率 =================

    int drawPulse(Canvas canvas, float[] filt, int W, float top, float bot) {
        if (filt.length < 10) return 0;
        float pp = WaveView.peakToPeak(filt);
        float[] norm = WaveView.normalize(filt);

        Paint p = new Paint(Paint.ANTI_ALIAS_FLAG);
        p.setColor(GREEN);
        p.setStrokeWidth(2f);
        p.setStyle(Paint.Style.STROKE);
        float x0 = 0f, y0 = bot - norm[0] * (bot - top);
        for (int i = 1; i < norm.length; i++) {
            float x1 = (float) i / (norm.length - 1) * W;
            float y1 = bot - norm[i] * (bot - top);
            canvas.drawLine(x0, y0, x1, y1, p);
            x0 = x1;
            y0 = y1;
        }
        if (pp < MIN_PP) return 0;

        List<Integer> peaks = findPeaks(filt, 22, 0.5f);
        Paint dot = new Paint(Paint.ANTI_ALIAS_FLAG);
        dot.setColor(GREEN);
        for (int i = 0; i < peaks.size(); i++) {
            int idx = peaks.get(i).intValue();
            float x = (float) idx / (filt.length - 1) * W;
            float y = bot - norm[idx] * (bot - top);
            canvas.drawCircle(x, y, 3.2f, dot);
        }
        Integer bpm = estimateBpm(peaks);
        return bpm == null ? 0 : bpm.intValue();
    }

    static List<Integer> findPeaks(float[] sig, int minGap, float thrRatio) {
        List<Integer> peaks = new ArrayList<>();
        if (sig.length < 3) return peaks;
        float mx = -Float.MAX_VALUE, mn = Float.MAX_VALUE;
        for (int i = 0; i < sig.length; i++) {
            if (sig[i] > mx) mx = sig[i];
            if (sig[i] < mn) mn = sig[i];
        }
        float pp = mx - mn;
        if (pp < MIN_PP) return peaks;
        float thr = mn + pp * thrRatio;
        int last = -100000;
        for (int i = 1; i < sig.length - 1; i++) {
            if (sig[i] > thr && sig[i] >= sig[i - 1] && sig[i] > sig[i + 1]
                    && (i - last) >= minGap) {
                peaks.add(Integer.valueOf(i));
                last = i;
            }
        }
        return peaks;
    }

    static Integer estimateBpm(List<Integer> peaks) {
        if (peaks.size() < 3) return null;
        List<Integer> gaps = new ArrayList<>();
        for (int i = 1; i < peaks.size(); i++) {
            gaps.add(Integer.valueOf(peaks.get(i).intValue() - peaks.get(i - 1).intValue()));
        }
        List<Integer> sorted = new ArrayList<>(gaps);
        Collections.sort(sorted);
        int med = sorted.get(sorted.size() / 2).intValue();
        if (med <= 0) return null;
        List<Integer> good = new ArrayList<>();
        for (int i = 0; i < gaps.size(); i++) {
            int g = gaps.get(i).intValue();
            if (Math.abs(g - med) < med * 0.35) good.add(Integer.valueOf(g));
        }
        if (good.size() < gaps.size() * 0.6) return null;
        float avg = 0;
        for (int i = 0; i < good.size(); i++) avg += good.get(i).intValue();
        avg /= good.size();
        float bpm = 60f / (avg / SAMPLE_RATE);
        if (bpm < 35 || bpm > 200) return null;
        return Integer.valueOf((int) Math.round(bpm));
    }
}
