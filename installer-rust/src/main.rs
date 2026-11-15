// ETS2 Radio DJ — установщик на Rust (eframe/egui)
// Стиль сайта: тёмный #121212 + зелёный #1DB954.
// Все внешние процессы — с CREATE_NO_WINDOW, PowerShell не видно.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use eframe::egui;
use std::os::windows::process::CommandExt;
use std::sync::{Arc, Mutex};

// bundle с файлами проекта вшит прямо в exe — «один файл и всё работает»
const BUNDLE: &[u8] = include_bytes!("../../ETS2RadioDJ_bundle.zip");
const STREAM_ENTRY: &str = "http://127.0.0.1:5000/radio.mp3|YT Automix DJ|Custom|RU|128|1";
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

const ACCENT: egui::Color32 = egui::Color32::from_rgb(0x1D, 0xB9, 0x54);
const ACCENT_DARK: egui::Color32 = egui::Color32::from_rgb(0x0A, 0x58, 0x2A);
const BG: egui::Color32 = egui::Color32::from_rgb(0x12, 0x12, 0x12);
const CARD: egui::Color32 = egui::Color32::from_rgb(0x24, 0x24, 0x24);
const TEXT: egui::Color32 = egui::Color32::WHITE;
const MUTED: egui::Color32 = egui::Color32::from_rgb(0xC4, 0xC8, 0xD0);
const DIM: egui::Color32 = egui::Color32::from_rgb(0x6F, 0x74, 0x7D);
const YELLOW: egui::Color32 = egui::Color32::from_rgb(0xF5, 0xA6, 0x23);
const RED: egui::Color32 = egui::Color32::from_rgb(0xFF, 0x5C, 0x5C);

const STEP_NAMES: [&str; 7] = [
    "Файлы проекта",
    "Python",
    "Зависимости (flask, yt-dlp, ytmusicapi)",
    "Виджет Xbox Game Bar",
    "Медиа-кнопки в автозапуск",
    "Радио в live_streams.sii игры",
    "Запуск сервера",
];

#[derive(Clone, Copy, PartialEq)]
enum StepState {
    Pending,
    Active,
    Done,
    Warn,
    Fail,
}

#[derive(Clone)]
struct Step {
    state: StepState,
    detail: String,
}

impl Step {
    fn new() -> Self {
        Self { state: StepState::Pending, detail: String::new() }
    }
}

struct AppState {
    steps: [Step; 7],
    progress: f32,
    running: bool,
    done: bool,
    fail: bool,
    detail: String,
}

impl AppState {
    fn new() -> Self {
        Self {
            steps: std::array::from_fn(|_| Step::new()),
            progress: 0.0,
            running: false,
            done: false,
            fail: false,
            detail: String::new(),
        }
    }
    fn mark(&mut self, idx: usize, state: StepState, detail: &str) {
        self.steps[idx].state = state;
        self.steps[idx].detail = detail.to_string();
        if matches!(state, StepState::Done | StepState::Warn) {
            self.progress = self.progress.max((idx + 1) as f32 / 7.0);
        }
    }
}

fn hidden_command(program: &str, args: &[&str]) -> std::process::Command {
    use std::os::windows::process::CommandExt;
    let mut c = std::process::Command::new(program);
    c.args(args).creation_flags(CREATE_NO_WINDOW);
    c
}

fn run_hidden(program: &str, args: &[&str]) -> Result<i32, String> {
    let status = hidden_command(program, args)
        .status()
        .map_err(|e| format!("{}: {}", program, e))?;
    Ok(status.code().unwrap_or(-1))
}

fn install_dir() -> std::path::PathBuf {
    let local = std::env::var("LOCALAPPDATA").unwrap_or_else(|_| {
        format!("{}\\AppData\\Local", std::env::var("USERPROFILE").unwrap_or_default())
    });
    std::path::PathBuf::from(local).join("ETS2RadioDJ")
}

fn find_python() -> Option<String> {
    for c in ["py", "python"] {
        if let Ok(out) = hidden_command("where", &[c]).output() {
            if out.status.success() && !out.stdout.is_empty() {
                return Some(c.to_string());
            }
        }
    }
    None
}

fn step_extract(app_dir: &std::path::Path) -> Result<String, String> {
    if app_dir.join("server.py").exists() {
        return Ok("уже установлено".into());
    }
    let cursor = std::io::Cursor::new(BUNDLE);
    let mut archive = zip::ZipArchive::new(cursor).map_err(|e| format!("zip: {}", e))?;
    std::fs::create_dir_all(app_dir).map_err(|e| format!("mkdir: {}", e))?;
    for i in 0..archive.len() {
        let mut file = archive.by_index(i).map_err(|e| format!("zip: {}", e))?;
        let Some(rel) = file.enclosed_name() else { continue };
        let out_path = app_dir.join(rel);
        if file.is_dir() {
            std::fs::create_dir_all(&out_path).map_err(|e| format!("mkdir: {}", e))?;
            continue;
        }
        if let Some(parent) = out_path.parent() {
            std::fs::create_dir_all(parent).map_err(|e| format!("mkdir: {}", e))?;
        }
        let mut out = std::fs::File::create(&out_path).map_err(|e| format!("create: {}", e))?;
        std::io::copy(&mut file, &mut out).map_err(|e| format!("copy: {}", e))?;
    }
    Ok("распаковано из встроенного архива".into())
}

fn step_python() -> Result<String, String> {
    if let Some(p) = find_python() {
        return Ok(p);
    }
    run_hidden("winget", &[
        "install", "-e", "--id", "Python.Python.3.12",
        "--accept-package-agreements", "--accept-source-agreements",
    ]).map_err(|e| format!("winget: {}", e))?;
    find_python().ok_or_else(|| "Python не установлен — скачай с python.org и перезапусти".into())
}

fn step_pip(py: &str, app_dir: &std::path::Path) -> Result<(), String> {
    let req = app_dir.join("requirements.txt");
    let code = run_hidden(py, &[
        "-m", "pip", "install", "-r", req.to_str().unwrap_or("requirements.txt"),
        "--quiet", "--disable-pip-version-check",
    ])?;
    if code != 0 {
        return Err(format!("pip завершился с ошибкой (код {})", code));
    }
    Ok(())
}

fn step_widget(app_dir: &std::path::Path) -> Result<String, String> {
    let _ = hidden_command("taskkill", &["/F", "/IM", "RadioWidget.exe"]).output();
    let manifest = app_dir.join("RadioWidget\\AppPackages\\RadioWidget_Registered\\AppxManifest.xml");
    if !manifest.exists() {
        return Ok("пакет виджета не найден — пропущено".into());
    }
    let ps_script = format!(
        "Add-AppxPackage -Register '{}' -ForceApplicationShutdown",
        manifest.display()
    );
    let _ = run_hidden("powershell", &[
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", &ps_script,
    ])?;
    let check = hidden_command("powershell", &[
        "-NoProfile", "-Command",
        "if (Get-AppxPackage -Name RadioWidget) { 'yes' } else { 'no' }",
    ]).output().map_err(|e| format!("powershell: {}", e))?;
    let ok = String::from_utf8_lossy(&check.stdout).trim() == "yes";
    if ok {
        Ok("виджет установлен (Win+G)".into())
    } else {
        Ok("виджет не зарегистрировался (нужна Win10/11)".into())
    }
}

fn step_smtc(app_dir: &std::path::Path) -> Result<String, String> {
    let bridge = app_dir.join("smtc_bridge.ps1");
    if !bridge.exists() {
        return Ok("мост не найден — пропущено".into());
    }
    // Планировщик надёжнее ярлыка: запускается при входе в систему
    let tr = format!("powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File \"{}\"", bridge.display());
    let _ = run_hidden("schtasks", &[
        "/Create", "/F", "/TN", "ETS2 Radio SMTC", "/TR", &tr, "/SC", "ONLOGON",
    ]);
    Ok("медиа-кнопки в автозапуске".into())
}

fn short_game(name: &str) -> &'static str {
    if name.contains("Euro") { "ETS2" } else { "ATS" }
}

fn step_ets2() -> Result<String, String> {
    let mut roots: Vec<std::path::PathBuf> = Vec::new();
    if let Ok(up) = std::env::var("USERPROFILE") {
        roots.push(std::path::PathBuf::from(up).join("Documents"));
    }
    if let Ok(od) = std::env::var("OneDrive") {
        let p = std::path::PathBuf::from(od).join("Documents");
        if p.exists() { roots.push(p); }
    }
    let mut summary: Vec<String> = Vec::new();
    for root in &roots {
        for game in ["Euro Truck Simulator 2", "American Truck Simulator"] {
            let sii = root.join(game).join("live_streams.sii");
            if !sii.exists() { continue; }
            let game_label = short_game(game);
            let content = std::fs::read_to_string(&sii).unwrap_or_default();
            if content.contains(STREAM_ENTRY) {
                summary.push(format!("{}: уже есть", game_label));
                continue;
            }
            let _ = std::fs::copy(&sii, sii.with_extension("bak"));
            let mut lines: Vec<String> = content.lines().map(|s| s.to_string()).collect();
            let mut inserted = false;
            for i in 0..lines.len() {
                let line = &lines[i];
                let trimmed = line.trim_start();
                if trimmed.starts_with("live_streams:") && trimmed.contains('(') {
                    let num_part: String = trimmed
                        .chars()
                        .skip_while(|c| *c != '(')
                        .skip(1)
                        .take_while(|c| c.is_ascii_digit())
                        .collect();
                    if let Ok(n) = num_part.parse::<i32>() {
                        lines[i] = line.replacen(&format!("({}", num_part), &format!("({}", n + 1), 1);
                    }
                    lines.insert(i + 1, format!("\t.stream_data[]: \"{}\",", STREAM_ENTRY));
                    inserted = true;
                    break;
                }
            }
            if !inserted {
                for i in (0..lines.len()).rev() {
                    if lines[i].contains("stream_data[]:") {
                        lines.insert(i + 1, format!("\t.stream_data[]: \"{}\",", STREAM_ENTRY));
                        inserted = true;
                        break;
                    }
                }
            }
            if !inserted {
                summary.push(format!("{}: не нашёл куда вставить", game_label));
                continue;
            }
            let mut out = lines.join("\n");
            if content.contains("\r\n") {
                out = out.replace('\n', "\r\n");
            }
            std::fs::write(&sii, out).map_err(|e| format!("write: {}", e))?;
            summary.push(format!("{}: прописано", game_label));
        }
    }
    if summary.is_empty() {
        return Ok("игра не найдена в Documents — пропиши кнопкой в настройках".into());
    }
    Ok(summary.join("; "))
}

fn step_start_server(app_dir: &std::path::Path) -> Result<String, String> {
    let busy = std::net::TcpStream::connect("127.0.0.1:5000").is_ok();
    if !busy {
        let bat = app_dir.join("start_radio.bat");
        let _ = hidden_command("cmd", &[
            "/c", "start", "ETS2RadioServer", "/min",
            bat.to_str().unwrap_or("start_radio.bat"),
        ]).spawn();
        std::thread::sleep(std::time::Duration::from_secs(5));
    }
    if std::net::TcpStream::connect("127.0.0.1:5000").is_ok() {
        Ok("сервер работает — http://127.0.0.1:5000".into())
    } else {
        Ok("сервер не поднялся — запусти start_radio.bat вручную".into())
    }
}

fn run_install(state: Arc<Mutex<AppState>>) {
    let app_dir = install_dir();
    let _ = std::fs::create_dir_all(&app_dir);
    {
        let mut st = state.lock().unwrap();
        st.running = true;
        st.fail = false;
        st.done = false;
        st.detail = String::new();
        st.progress = 0.0;
        for s in st.steps.iter_mut() { *s = Step::new(); }
        st.steps[0].state = StepState::Active;
    }

    let result: Result<(), String> = (|| {
        // 1. Файлы
        match step_extract(&app_dir) {
            Ok(d) => state.lock().unwrap().mark(0, StepState::Done, &d),
            Err(e) => return Err(e),
        }
        // 2. Python
        state.lock().unwrap().mark(1, StepState::Active, "");
        let py = step_python()?;
        state.lock().unwrap().mark(1, StepState::Done, &py);
        // 3. Зависимости
        state.lock().unwrap().mark(2, StepState::Active, "");
        step_pip(&py, &app_dir)?;
        state.lock().unwrap().mark(2, StepState::Done, "");
        // 4. Виджет
        state.lock().unwrap().mark(3, StepState::Active, "");
        let d = step_widget(&app_dir)?;
        state.lock().unwrap().mark(3, StepState::Done, &d);
        // 5. SMTC
        state.lock().unwrap().mark(4, StepState::Active, "");
        let d = step_smtc(&app_dir)?;
        state.lock().unwrap().mark(4, StepState::Done, &d);
        // 6. ETS2
        state.lock().unwrap().mark(5, StepState::Active, "");
        let d = step_ets2()?;
        state.lock().unwrap().mark(5, StepState::Done, &d);
        // 7. Сервер
        state.lock().unwrap().mark(6, StepState::Active, "");
        let d = step_start_server(&app_dir)?;
        state.lock().unwrap().mark(6, StepState::Done, &d);
        Ok(())
    })();

    let mut st = state.lock().unwrap();
    st.running = false;
    st.progress = 1.0;
    st.done = true;
    if let Err(e) = result {
        st.fail = true;
        st.detail = e;
        // пометить активный шаг как провальный
        if let Some(i) = st.steps.iter().position(|s| s.state == StepState::Active) {
            st.steps[i].state = StepState::Fail;
        }
    }
}

struct InstallerApp {
    state: Arc<Mutex<AppState>>,
    started: bool,
}

impl eframe::App for InstallerApp {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        let running = self.state.lock().unwrap().running;
        if running {
            ctx.request_repaint_after(std::time::Duration::from_millis(150));
        }

        egui::CentralPanel::default()
            .frame(egui::Frame::none().fill(BG).inner_margin(0.0))
            .show(ctx, |ui| {
                let (rect, _) = ui.allocate_exact_size(egui::vec2(ui.available_width(), 96.0), egui::Sense::hover());
                // зелёный градиент: два слоя
                ui.painter().rect_filled(rect, egui::Rounding::ZERO, ACCENT);
                ui.painter().rect_filled(rect, egui::Rounding::ZERO, egui::Color32::from_rgba_premultiplied(5, 40, 20, 90));

                ui.painter().circle_filled(
                    rect.left_top() + egui::vec2(52.0, 48.0),
                    28.0,
                    egui::Color32::from_rgb(16, 16, 16),
                );
                ui.painter().text(
                    rect.left_top() + egui::vec2(52.0, 48.0),
                    egui::Align2::CENTER_CENTER,
                    "♪",
                    egui::FontId::proportional(28.0),
                    ACCENT,
                );
                ui.painter().text(
                    rect.left_top() + egui::vec2(98.0, 34.0),
                    egui::Align2::LEFT_CENTER,
                    "ETS2 RADIO DJ",
                    egui::FontId::proportional(22.0),
                    TEXT,
                );
                ui.painter().text(
                    rect.left_top() + egui::vec2(100.0, 64.0),
                    egui::Align2::LEFT_CENTER,
                    "Личное радио YouTube Music для Euro Truck Simulator 2",
                    egui::FontId::proportional(12.5),
                    egui::Color32::from_rgb(226, 242, 232),
                );

                let st = self.state.lock().unwrap();

                // Шаги-чеклист
                ui.add_space(16.0);
                for (i, step) in st.steps.iter().enumerate() {
                    ui.horizontal(|ui| {
                        ui.add_space(24.0);
                        let (glyph, glyph_color, text_color) = match step.state {
                            StepState::Pending => ("○", DIM, DIM),
                            StepState::Active => ("▸", TEXT, TEXT),
                            StepState::Done => ("✓", ACCENT, ACCENT),
                            StepState::Warn => ("✓", YELLOW, YELLOW),
                            StepState::Fail => ("✕", RED, RED),
                        };
                        ui.label(egui::RichText::new(glyph).color(glyph_color).size(14.0));
                        ui.label(egui::RichText::new(STEP_NAMES[i]).color(text_color).size(14.0));
                        if !step.detail.is_empty() && step.state != StepState::Pending {
                            ui.label(
                                egui::RichText::new(format!("({})", step.detail))
                                    .size(11.0)
                                    .color(MUTED),
                            );
                        }
                    });
                    ui.add_space(5.0);
                }

                ui.add_space(8.0);
                // Прогресс-бар
                let (bar_rect, _) = ui.allocate_exact_size(egui::vec2(ui.available_width() - 40.0, 8.0), egui::Sense::hover());
                ui.painter().rect_filled(bar_rect, egui::Rounding::same(4.0), CARD);
                if st.progress > 0.0 {
                    ui.painter().rect_filled(
                        egui::Rect::from_min_size(
                            bar_rect.min,
                            egui::vec2(bar_rect.width() * st.progress.min(1.0), 8.0),
                        ),
                        egui::Rounding::same(4.0),
                        ACCENT,
                    );
                }

                ui.add_space(10.0);
                // Статус
                let (msg, msg_color) = if st.fail {
                    (format!("Ошибка: {}", st.detail), RED)
                } else if st.done {
                    ("Установка завершена — приятной дороги!".to_string(), ACCENT)
                } else if st.running {
                    let active_idx = st.steps.iter().position(|s| s.state == StepState::Active).unwrap_or(0);
                    (format!("{}...", STEP_NAMES[active_idx]), MUTED)
                } else {
                    ("Нажми «Установить» — остальное сделаю я.".to_string(), MUTED)
                };
                ui.label(
                    egui::RichText::new(msg)
                        .color(msg_color)
                        .size(13.0)
                        .strong(),
                );

                ui.with_layout(egui::Layout::bottom_up(egui::Align::Min), |ui| {
                    ui.add_space(14.0);
                    ui.horizontal(|ui| {
                        ui.add_space(20.0);
                        let label = if st.done && !st.fail { "ГОТОВО" } else { "УСТАНОВИТЬ" };
                        let btn = egui::Button::new(
                            egui::RichText::new(label).size(15.0).strong().color(egui::Color32::from_rgb(10, 10, 10)),
                        )
                        .min_size(egui::vec2(210.0, 44.0))
                        .fill(ACCENT)
                        .rounding(8.0);
                        let install_clicked = ui.add_enabled(!st.running, btn).clicked();
                        ui.add_space(12.0);
                        let open = egui::Button::new(
                            egui::RichText::new("Открыть приложение").size(13.0).color(TEXT),
                        )
                        .min_size(egui::vec2(200.0, 44.0))
                        .fill(CARD)
                        .rounding(8.0);
                        let open_clicked = ui.add_enabled(st.done && !st.fail, open).clicked();
                        ui.add_space(12.0);
                        let quit = egui::Button::new(
                            egui::RichText::new("Выход").size(13.0).color(MUTED),
                        )
                        .min_size(egui::vec2(110.0, 44.0))
                        .fill(CARD)
                        .rounding(8.0);
                        if ui.add(quit).clicked() {
                            std::process::exit(0);
                        }
                        if install_clicked && !st.done {
                            let state = self.state.clone();
                            self.started = true;
                            std::thread::spawn(move || run_install(state));
                        }
                        if open_clicked {
                            let _ = std::process::Command::new("cmd")
                                .args(["/c", "start", "", "http://127.0.0.1:5000"])
                                .creation_flags(CREATE_NO_WINDOW)
                                .spawn();
                        }
                    });
                });
            });
    }
}

fn main() -> eframe::Result<()> {
    let state = Arc::new(Mutex::new(AppState::new()));
    let options = eframe::NativeOptions {
        viewport: egui::ViewportBuilder::default()
            .with_title("ETS2 Radio DJ — установка")
            .with_inner_size([700.0, 600.0])
            .with_icon(egui::IconData {
                width: 32,
                height: 32,
                rgba: icon_rgba_32(),
            }),
        ..Default::default()
    };
    eframe::run_native(
        "ETS2 Radio DJ — установка",
        options,
        Box::new(move |cc| {
            let mut style = (*cc.egui_ctx.style()).clone();
            style.visuals.dark_mode = true;
            style.visuals.panel_fill = BG;
            style.visuals.window_fill = BG;
            style.visuals.extreme_bg_color = CARD;
            style.visuals.widgets.noninteractive.bg_fill = BG;
            style.visuals.widgets.inactive.bg_fill = CARD;
            style.visuals.widgets.hovered.bg_fill = CARD;
            style.visuals.widgets.active.bg_fill = CARD;
            style.visuals.selection.stroke.color = ACCENT;
            style.spacing.item_spacing = egui::vec2(8.0, 4.0);
            cc.egui_ctx.set_style(style);
            Box::new(InstallerApp { state, started: false })
        }),
    )
}

// Рисуем 32x32 иконку программно: тёмный квадрат + зелёный круг + «нота» из прямоугольников
fn icon_rgba_32() -> Vec<u8> {
    let mut rgba = vec![0u8; 32 * 32 * 4];
    let mut set = |x: i32, y: i32, c: [u8; 4]| {
        if (0..32).contains(&x) && (0..32).contains(&y) {
            let i = ((y as usize) * 32 + (x as usize)) * 4;
            rgba[i] = c[0]; rgba[i + 1] = c[1]; rgba[i + 2] = c[2]; rgba[i + 3] = c[3];
        }
    };
    let bg = [0x12u8, 0x12, 0x12, 0xFF];
    for y in 0..32 {
        for x in 0..32 {
            // скруглённые углы
            let corner = |cx: i32, cy: i32| -> bool {
                let dx = (x - cx) * (x - cx);
                let dy = (y - cy) * (y - cy);
                dx + dy > 81
            };
            let in_corner = (corner(5, 5) && x < 8 && y < 8)
                || (corner(26, 5) && x >= 24 && y < 8)
                || (corner(5, 26) && x < 8 && y >= 24)
                || (corner(26, 26) && x >= 24 && y >= 24);
            if !in_corner {
                set(x, y, bg);
            }
        }
    }
    let acc = [0x1Du8, 0xB9, 0x54, 0xFF];
    let dark = [0x12u8, 0x12, 0x12, 0xFF];
    // зелёный круг
    for y in 0..32 {
        for x in 0..32 {
            let dx = x - 16; let dy = y - 16;
            if dx * dx + dy * dy <= 121 {
                set(x, y, acc);
            }
        }
    }
    // «нота»: круг + палка
    for y in 0..32 {
        for x in 0..32 {
            let dx = x - 13; let dy = y - 22;
            if dx * dx + dy * dy <= 12 {
                set(x, y, dark);
            }
        }
    }
    for y in 8..24 { set(18, y, dark); set(19, y, dark); }
    for y in 6..14 { for x in 18..22 { set(x, y, dark); } }
    rgba
}
