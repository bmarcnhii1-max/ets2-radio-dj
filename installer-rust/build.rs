fn main() {
    // Иконка exe в стиле сайта
    if std::path::Path::new("../install_icon.ico").exists() {
        let mut res = winres::WindowsResource::new();
        res.set_icon("../install_icon.ico");
        res.compile().expect("failed to compile resources");
    }
}
