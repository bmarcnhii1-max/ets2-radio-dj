// ETS2 Radio DJ — установщик на Rust (eframe/egui)
// Стиль сайта: тёмный #0a0d12 + зелёный градиент #1db954→#0bc2b1.
// Кнопки закреплены к низу окна (TopBottomPanel), контент в ScrollArea —
// при любом размере окна ничего не уезжает за край.
// Все внешние процессы — с CREATE_NO_WINDOW, PowerShell не видно.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use eframe::egui;
use std::os::windows::process::CommandExt;
use std::sync::{Arc, Mutex};

// bundle с файлами проекта вшит прямо в exe — «один файл и всё работает»
const BUNDLE: &[u8] = include_bytes!("../../ETS2RadioDJ_bundle.zip");
const STREAM_ENTRY: &str = "http://127.0.0.1:5000/radio.mp3|YT Automix DJ|Custom|RU|128|1";
const CREATE_NO_WINDOW: u32 = 0x0800_0000;
const APP_VERSION: &str = "1.1";

const ACCENT: egui::Color32 = egui::Color32::from_rgb(0x1D, 0xB9, 0x54);

const BG: egui::Color32 = egui::Color32::from_rgb(0x0A, 0x0D, 0x12);
const BAR_BG: egui::Color32 = egui::Color32::from_rgb(0x0D, 0x11, 0x18);
const CARD: egui::Color32 = egui::Color32::from_rgb(0x13, 0x18, 0x22);
const CARD2: egui::Color32 = egui::Color32::from_rgb(0x1A, 0x21, 0x30);
const TEXT: egui::Color32 = egui::Color32::from_rgb(0xF2, 0xF5, 0xF9);
const MUTED: egui::Color32 = egui::Color32::from_rgb(0x9A, 0xA5, 0xB5);
const DIM: egui::Color32 = egui::Color32::from_rgb(0x5C, 0x66, 0x75);
const YELLOW: egui::Color32 = egui::Color32::from_rgb(0xF5, 0xA6, 0x23);
const RED: egui::Color32 = egui::Color32::from_rgb(0xFF, 0x5C, 0x5C);
const ON_ACCENT: egui::Color32 = egui::Color32::from_rgb(0x04, 0x12, 0x0A);

const STEP_NAMES: [&str; 7] = [
    "Файлы проекта",
    "Python",
    "Зависимости (flask, yt-dlp, ytmusicapi)",
    "Виджет Xbox Game Bar",
    "Медиа-кнопки в автозапуск",
    "Радио в live_streams.sii игры",
    "Запуск сервера",
];

const SPINNER: [&str; 10] = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"];

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
        if state == StepState::Fail {
            self.progress = self.progress.max(idx as f32 / 7.0);
        }
    }
    fn reset(&mut self) {
        self.progress = 0.0;
        self.running = true;
        self.done = false;
        self.fail = false;
        self.detail = String::new();
        for s in self.steps.iter_mut() {
            *s = Step::new();
        }
        self.steps[0].state = StepState::Active;
    }
}

fn hidden_command(program: &str, args: &[&str]) -> std::process::Command {
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

// Всегда распаковываем заново: повторный запуск установщика = обновление файлов.
// Пользовательские данные (config.json, токены, профили) в bundle не входят — не трогаются.
fn step_extract(app_dir: &std::path::Path) -> Result<String, String> {
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
    Ok(format!("v{} — распаковано в AppData", APP_VERSION))
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
    state.lock().unwrap().reset();

    let result: Result<(), String> = (|| {
        // 1. Файлы (всегда перезаписываются — повторный запуск = обновление)
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
        // 4. Виджет (не зарегистрировался — предупреждение, не провал)
        state.lock().unwrap().mark(3, StepState::Active, "");
        let d = step_widget(&app_dir)?;
        let ws = if d.contains("не зарегистрировался") || d.contains("не найден") {
            StepState::Warn
        } else {
            StepState::Done
        };
        state.lock().unwrap().mark(3, ws, &d);
        // 5. SMTC
        state.lock().unwrap().mark(4, StepState::Active, "");
        let d = step_smtc(&app_dir)?;
        let ws = if d.contains("не найден") { StepState::Warn } else { StepState::Done };
        state.lock().unwrap().mark(4, ws, &d);
        // 6. ETS2 (игра не найдена — предупреждение)
        state.lock().unwrap().mark(5, StepState::Active, "");
        let d = step_ets2()?;
        let ws = if d.contains("не найдена") || d.contains("не нашёл") {
            StepState::Warn
        } else {
            StepState::Done
        };
        state.lock().unwrap().mark(5, ws, &d);
        // 7. Сервер (не поднялся — предупреждение)
        state.lock().unwrap().mark(6, StepState::Active, "");
        let d = step_start_server(&app_dir)?;
        let ws = if d.contains("не поднялся") { StepState::Warn } else { StepState::Done };
        state.lock().unwrap().mark(6, ws, &d);
        Ok(())
    })();

    let mut st = state.lock().unwrap();
    st.running = false;
    st.progress = if result.is_err() { st.progress.max(0.99) } else { 1.0 };
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
}

// Кружок состояния шага: цвет + глиф
fn draw_step_icon(ui: &egui::Ui, center: egui::Pos2, state: StepState, spinner: &str) {
    let p = ui.painter();
    match state {
        StepState::Pending => {
            p.circle_stroke(center, 7.0, egui::Stroke::new(1.3_f32, DIM));
        }
        StepState::Active => {
            p.circle_stroke(center, 7.0, egui::Stroke::new(1.3_f32, ACCENT));
            p.text(center, egui::Align2::CENTER_CENTER, spinner, egui::FontId::proportional(11.0), ACCENT);
        }
        StepState::Done => {
            p.circle_filled(center, 7.0, ACCENT);
            p.text(center, egui::Align2::CENTER_CENTER, "✓", egui::FontId::proportional(10.0), ON_ACCENT);
        }
        StepState::Warn => {
            p.circle_filled(center, 7.0, YELLOW);
            p.text(center, egui::Align2::CENTER_CENTER, "✓", egui::FontId::proportional(10.0), ON_ACCENT);
        }
        StepState::Fail => {
            p.circle_filled(center, 7.0, RED);
            p.text(center, egui::Align2::CENTER_CENTER, "✕", egui::FontId::proportional(9.0), TEXT);
        }
    }
}

impl eframe::App for InstallerApp {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        let running = self.state.lock().unwrap().running;
        if running {
            ctx.request_repaint_after(std::time::Duration::from_millis(90));
        }
        let spinner = {
            let t = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap_or_default();
            SPINNER[(t.subsec_millis() / 90) as usize % SPINNER.len()]
        };

        // ---- Нижняя панель с кнопками: всегда прижата к низу окна ----
        egui::TopBottomPanel::bottom("actions")
            .exact_height(84.0)
            .frame(egui::Frame::none().fill(BAR_BG))
            .show(ctx, |ui| {
                // тонкая линия-граница сверху панели
                let rect = ui.available_rect_before_wrap();
                ui.painter().line_segment(
                    [rect.left_top(), rect.right_top()],
                    egui::Stroke::new(1.0_f32, egui::Color32::from_rgb(0x20, 0x28, 0x36)),
                );
                ui.add_space(14.0);
                let st = self.state.lock().unwrap();
                ui.horizontal(|ui| {
                    ui.add_space(20.0);
                    let install_label = if st.done && !st.fail { "УСТАНОВИТЬ ЗАНОВО" } else { "УСТАНОВИТЬ" };
                    let install_btn = egui::Button::new(
                        egui::RichText::new(install_label).size(14.5).strong().color(ON_ACCENT),
                    )
                    .min_size(egui::vec2(220.0, 46.0))
                    .fill(ACCENT)
                    .rounding(10.0);
                    if ui.add_enabled(!st.running, install_btn).clicked() {
                        let state = self.state.clone();
                        std::thread::spawn(move || run_install(state));
                    }
                    ui.add_space(10.0);
                    let open_enabled = st.done && !st.fail && !st.running;
                    let mut open_btn = egui::Button::new(
                        egui::RichText::new("Открыть приложение").size(13.0).color(if open_enabled { TEXT } else { DIM }),
                    )
                    .min_size(egui::vec2(180.0, 46.0))
                    .fill(CARD2)
                    .rounding(10.0);
                    if open_enabled {
                        open_btn = open_btn.stroke(egui::Stroke::new(1.0_f32, ACCENT.gamma_multiply(0.5)));
                    }
                    if ui.add_enabled(open_enabled, open_btn).clicked() {
                        let _ = std::process::Command::new("cmd")
                            .args(["/c", "start", "", "http://127.0.0.1:5000"])
                            .creation_flags(CREATE_NO_WINDOW)
                            .spawn();
                    }
                    // Выход прижат вправо
                    ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                        ui.add_space(20.0);
                        let quit = egui::Button::new(
                            egui::RichText::new("Выход").size(13.0).color(MUTED),
                        )
                        .min_size(egui::vec2(96.0, 46.0))
                        .fill(CARD2)
                        .rounding(10.0);
                        if ui.add(quit).clicked() {
                            std::process::exit(0);
                        }
                    });
                });
            });

        // ---- Контент: скроллится, если окна вдруг мало ----
        egui::CentralPanel::default()
            .frame(egui::Frame::none().fill(BG))
            .show(ctx, |ui| {
                egui::ScrollArea::vertical().auto_shrink(false).show(ui, |ui| {
                    egui::Frame::none()
                        .inner_margin(egui::Margin::symmetric(22.0, 18.0))
                        .show(ui, |ui| {
                            let st = self.state.lock().unwrap();

                            // ---- HERO: зелёный градиент, логотип, версия ----
                            let (hero, _) = ui.allocate_exact_size(
                                egui::vec2(ui.available_width(), 108.0),
                                egui::Sense::hover(),
                            );
                            let p = ui.painter();
                            p.rect_filled(hero, egui::Rounding::same(16.0), ACCENT);
                            p.rect_filled(hero, egui::Rounding::same(16.0), egui::Color32::from_rgba_premultiplied(2, 24, 14, 110));
                            // декоративные круги справа
                            p.circle_filled(hero.right_center() + egui::vec2(46.0, -6.0), 34.0, egui::Color32::from_rgba_premultiplied(255, 255, 255, 16));
                            p.circle_filled(hero.right_center() + egui::vec2(110.0, 18.0), 22.0, egui::Color32::from_rgba_premultiplied(255, 255, 255, 12));
                            p.circle_filled(hero.right_center() + egui::vec2(80.0, -26.0), 12.0, egui::Color32::from_rgba_premultiplied(255, 255, 255, 20));

                            let logo_c = hero.left_center() + egui::vec2(42.0, 0.0);
                            p.circle_filled(logo_c, 26.0, BG);
                            p.text(logo_c, egui::Align2::CENTER_CENTER, "♪", egui::FontId::proportional(26.0), ACCENT);
                            p.text(
                                hero.left_top() + egui::vec2(84.0, 38.0),
                                egui::Align2::LEFT_CENTER,
                                "ETS2 RADIO DJ",
                                egui::FontId::proportional(23.0),
                                TEXT,
                            );
                            p.text(
                                hero.left_top() + egui::vec2(86.0, 66.0),
                                egui::Align2::LEFT_CENTER,
                                "Личное радио YouTube Music для Euro Truck Simulator 2",
                                egui::FontId::proportional(12.5),
                                egui::Color32::from_rgb(0xD7, 0xF5, 0xE3),
                            );
                            // чип версии справа сверху
                            let chip_c = hero.right_top() + egui::vec2(-30.0, 22.0);
                            p.rect_filled(
                                egui::Rect::from_center_size(chip_c, egui::vec2(46.0, 20.0)),
                                egui::Rounding::same(10.0),
                                egui::Color32::from_rgba_premultiplied(0, 0, 0, 90),
                            );
                            p.text(chip_c, egui::Align2::CENTER_CENTER, format!("v{}", APP_VERSION), egui::FontId::monospace(11.0), TEXT);

                            ui.add_space(14.0);

                            // ---- Карточка шагов ----
                            egui::Frame::none()
                                .fill(CARD)
                                .rounding(14.0)
                                .stroke(egui::Stroke::new(1.0_f32, egui::Color32::from_rgb(0x1F, 0x26, 0x33)))
                                .inner_margin(egui::Margin::symmetric(16.0, 14.0))
                                .show(ui, |ui| {
                                    ui.label(
                                        egui::RichText::new("ЧТО БУДЕТ УСТАНОВЛЕНО")
                                            .size(10.0)
                                            .strong()
                                            .color(DIM)
                                            .extra_letter_spacing(1.2),
                                    );
                                    ui.add_space(10.0);
                                    for (i, step) in st.steps.iter().enumerate() {
                                        ui.horizontal(|ui| {
                                            let (ir, _) = ui.allocate_exact_size(egui::vec2(18.0, 18.0), egui::Sense::hover());
                                            draw_step_icon(ui, ir.center(), step.state, spinner);
                                            ui.label(egui::RichText::new(STEP_NAMES[i]).size(13.5).color(match step.state {
                                                StepState::Pending => DIM,
                                                StepState::Active => TEXT,
                                                _ => step_color(step.state),
                                            }).strong());
                                            ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                                                if !step.detail.is_empty() && step.state != StepState::Pending {
                                                    ui.add(
                                                        egui::Label::new(
                                                            egui::RichText::new(step.detail.clone()).size(10.5).color(MUTED),
                                                        ).truncate(true),
                                                    );
                                                }
                                            });
                                        });
                                        ui.add_space(7.0);
                                    }
                                });

                            ui.add_space(14.0);

                            // ---- Прогресс ----
                            let (bar_rect, _) = ui.allocate_exact_size(
                                egui::vec2(ui.available_width() - 52.0, 7.0),
                                egui::Sense::hover(),
                            );
                            ui.painter().rect_filled(bar_rect, egui::Rounding::same(3.5), CARD2);
                            if st.progress > 0.0 {
                                ui.painter().rect_filled(
                                    egui::Rect::from_min_size(
                                        bar_rect.min,
                                        egui::vec2(bar_rect.width() * st.progress.min(1.0), 7.0),
                                    ),
                                    egui::Rounding::same(3.5),
                                    ACCENT,
                                );
                            }
                            let pct_text = format!("{}%", (st.progress * 100.0).round() as i32);
                            ui.painter().text(
                                egui::pos2(bar_rect.right() + 12.0, bar_rect.center().y),
                                egui::Align2::LEFT_CENTER,
                                pct_text,
                                egui::FontId::monospace(11.0),
                                MUTED,
                            );

                            ui.add_space(12.0);

                            // ---- Статус ----
                            let (msg, msg_color) = if st.fail {
                                (format!("Ошибка: {}", st.detail), RED)
                            } else if st.done {
                                ("Установка завершена — приятной дороги!".to_string(), ACCENT)
                            } else if st.running {
                                let active_idx = st.steps.iter().position(|s| s.state == StepState::Active).unwrap_or(0);
                                (format!("{} {}", spinner, STEP_NAMES[active_idx]), TEXT)
                            } else {
                                ("Нажми «Установить» — остальное сделаю я.".to_string(), MUTED)
                            };
                            ui.horizontal(|ui| {
                                let dot_color = if st.fail { RED } else if st.done { ACCENT } else if st.running { YELLOW } else { DIM };
                                let (dr, _) = ui.allocate_exact_size(egui::vec2(10.0, 10.0), egui::Sense::hover());
                                ui.painter().circle_filled(dr.center(), 4.0, dot_color);
                                ui.label(egui::RichText::new(msg).color(msg_color).size(13.0).strong());
                            });
                        });
                });
            });
    }
}

fn step_color(s: StepState) -> egui::Color32 {
    match s {
        StepState::Done => ACCENT,
        StepState::Warn => YELLOW,
        StepState::Fail => RED,
        _ => TEXT,
    }
}

fn main() -> eframe::Result<()> {
    let state = Arc::new(Mutex::new(AppState::new()));
    let options = eframe::NativeOptions {
        viewport: egui::ViewportBuilder::default()
            .with_title("ETS2 Radio DJ — установка")
            .with_inner_size([780.0, 640.0])
            .with_min_inner_size([640.0, 420.0])
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
            style.visuals.extreme_bg_color = CARD2;
            style.visuals.widgets.noninteractive.bg_fill = BG;
            style.visuals.widgets.inactive.bg_fill = CARD2;
            style.visuals.widgets.hovered.bg_fill = CARD2;
            style.visuals.widgets.active.bg_fill = CARD2;
            style.visuals.selection.stroke.color = ACCENT;
            style.spacing.item_spacing = egui::vec2(8.0, 4.0);
            cc.egui_ctx.set_style(style);
            Box::new(InstallerApp { state })
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
    let bg = [0x0Au8, 0x0D, 0x12, 0xFF];
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
    let dark = [0x0Au8, 0x0D, 0x12, 0xFF];
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
