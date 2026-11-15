using System;
using System.Collections.Generic;
using System.Linq;
using Windows.UI.Xaml;
using Windows.UI.Xaml.Controls;
using Windows.UI.Xaml.Input;
using Windows.UI.Xaml.Media;

namespace RadioWidget
{
    public class PlaylistItem
    {
        public string Id { get; set; }
        public string Title { get; set; }
        public override string ToString() => Title;
    }

    public class StationItem
    {
        public string Id { get; set; }
        public string Name { get; set; }
        public string Genre { get; set; }
    }

    public class TrackResult
    {
        public string VideoId { get; set; }
        public string Title { get; set; }
        public string Artist { get; set; }
        public string Thumbnail { get; set; }
    }

    public sealed partial class RadioPage : Page
    {
        private const string Api = "http://127.0.0.1:5000";
        private readonly Windows.Web.Http.HttpClient http = new Windows.Web.Http.HttpClient();
        private readonly DispatcherTimer timer = new DispatcherTimer();
        private readonly DispatcherTimer playlistTimer = new DispatcherTimer();
        private readonly DispatcherTimer radioTimer = new DispatcherTimer();
        private bool isPaused;
        private int currentTime;
        private int duration = 180;
        private string lastTitle = "";
        private string _lastQueueSig = null;
        private int pollFails;
        private string currentVideoId = "";
        private string currentTitle = "";
        private string currentArtist = "";
        private string currentThumbnail = "";
        private string currentAlbum = "";
        private string lastThumbnail = "";
        private string activeStationId = "";
        private bool trackIsStation;
        private bool cacheEnabled;
        private readonly Dictionary<string, Windows.Data.Json.IJsonValue> queueById = new Dictionary<string, Windows.Data.Json.IJsonValue>();
        private bool sliderSync;
        private bool sliderHeld;
        private bool volumeHeld;
        private int pendingSeek = -1;
        private DateTime lastUserSeek = DateTime.MinValue;
        private DateTime lastSeekSent = DateTime.MinValue;
        private int pendingVolume = -1;
        private DateTime lastVolumeSent = DateTime.MinValue;
        private double lastServerVolumePct = 100;
        private string notice = "";
        private DateTime noticeUntil = DateTime.MinValue;
        private string activeTab = "player";

        public RadioPage()
        {
            this.InitializeComponent();
            timer.Interval = TimeSpan.FromSeconds(1);
            timer.Tick += Timer_Tick;

            playlistTimer.Interval = TimeSpan.FromSeconds(30);
            playlistTimer.Tick += (s, e) => _ = RefreshPlaylistsAsync();

            radioTimer.Interval = TimeSpan.FromSeconds(10);
            radioTimer.Tick += (s, e) => { if (activeTab == "radio") _ = RefreshStationsAsync(); };

            this.Loaded += (s, e) =>
            {
                timer.Start();
                playlistTimer.Start();
                if (activeTab == "radio") radioTimer.Start();
                _ = RefreshPlaylistsAsync();
            };
            this.Unloaded += (s, e) =>
            {
                timer.Stop();
                playlistTimer.Stop();
                radioTimer.Stop();
            };
            _ = RefreshStatusAsync();
        }

        private void ShowNotice(string text)
        {
            notice = text ?? "";
            noticeUntil = DateTime.Now.AddSeconds(2);
        }

        private void ShowTab(string tab)
        {
            activeTab = tab;
            PlayerPanel.Visibility = tab == "player" ? Visibility.Visible : Visibility.Collapsed;
            QueuePanel.Visibility = tab == "queue" ? Visibility.Visible : Visibility.Collapsed;
            RadioPanel.Visibility = tab == "radio" ? Visibility.Visible : Visibility.Collapsed;
            SearchPanel.Visibility = tab == "search" ? Visibility.Visible : Visibility.Collapsed;

            SetTabStyle(TabPlayerBtn, tab == "player");
            SetTabStyle(TabQueueBtn, tab == "queue");
            SetTabStyle(TabRadioBtn, tab == "radio");
            SetTabStyle(TabSearchBtn, tab == "search");

            radioTimer.Stop();
            if (tab == "radio")
            {
                _ = RefreshStationsAsync();
                radioTimer.Start();
            }
            if (tab == "queue")
            {
                _ = RefreshStatusAsync();
            }
        }

        private void SetTabStyle(Button btn, bool active)
        {
            btn.Background = new SolidColorBrush(active
                ? Windows.UI.ColorHelper.FromArgb(255, 255, 255, 255)
                : Windows.UI.ColorHelper.FromArgb(255, 36, 36, 36));
            btn.Foreground = new SolidColorBrush(active
                ? Windows.UI.ColorHelper.FromArgb(255, 0, 0, 0)
                : Windows.UI.ColorHelper.FromArgb(255, 196, 200, 208));
        }

        private async System.Threading.Tasks.Task RefreshStatusAsync()
        {
            try
            {
                var resp = await http.GetAsync(new Uri(Api + "/api/status"));
                var json = await resp.Content.ReadAsStringAsync();
                var obj = Windows.Data.Json.JsonObject.Parse(json);
                var track = obj.GetNamedObject("track");

                string title = track.GetNamedString("title", "ETS2 Radio DJ");
                string artist = track.GetNamedString("artist", "");
                string thumbnail = track.GetNamedString("thumbnail", "");
                currentVideoId = track.GetNamedString("video_id", "");
                currentTitle = title;
                currentArtist = artist;
                currentThumbnail = thumbnail;
                currentAlbum = track.GetNamedString("album", "");
                trackIsStation = track.ContainsKey("is_station") && track.GetNamedBoolean("is_station", false);
                duration = (int)track.GetNamedNumber("duration", 180);

                if (!string.IsNullOrEmpty(notice) && DateTime.Now < noticeUntil)
                {
                    TitleText.Text = notice;
                    ArtistText.Text = title;
                }
                else
                {
                    TitleText.Text = title;
                    ArtistText.Text = artist;
                    BadgeText.Text = trackIsStation ? "РАДИО В ЭФИРЕ" : (currentAlbum.Length > 0 ? "В ЭФИРЕ: " + currentAlbum : "В ЭФИРЕ");
                }
                bool favorite = track.GetNamedBoolean("favorite", false);
                LikeBtn.Content = favorite ? "\uE735" : "\uE734";
                ToolTipService.SetToolTip(LikeBtn, favorite ? "Убрать лайк" : "Лайк в аккаунт");
                if (!string.IsNullOrEmpty(thumbnail) && thumbnail != lastThumbnail)
                {
                    try
                    {
                        // Декод в 128px: без этого каждая обложка грузится как
                        // 1280x720 (3.5 МБ на трек) и память растёт до 500+ МБ
                        var bmp = new Windows.UI.Xaml.Media.Imaging.BitmapImage();
                        bmp.DecodePixelWidth = 128;
                        bmp.DecodePixelHeight = 128;
                        bmp.DecodePixelType = Windows.UI.Xaml.Media.Imaging.DecodePixelType.Logical;
                        bmp.UriSource = new Uri(thumbnail);
                        AlbumArt.Source = bmp;
                        AlbumArt.Visibility = Visibility.Visible;
                        AlbumArtFallback.Visibility = Visibility.Collapsed;
                        lastThumbnail = thumbnail;
                        GC.Collect(1);
                    }
                    catch
                    {
                    }
                }
                else if (string.IsNullOrEmpty(thumbnail))
                {
                    AlbumArt.Visibility = Visibility.Collapsed;
                    AlbumArtFallback.Visibility = Visibility.Visible;
                    lastThumbnail = "";
                }
                if (obj.ContainsKey("queue"))
                {
                    UpdateQueue(obj.GetNamedArray("queue"));
                }
                else
                {
                    UpdateQueue(null);
                }
                isPaused = track.GetNamedBoolean("is_paused", false);
                currentTime = (int)track.GetNamedNumber("current_time", 0);
                if (trackIsStation) duration = 0;

                if (trackIsStation)
                {
                    CurrentTimeText.Text = "эфир";
                    DurationText.Text = "live";
                }
                else
                {
                    CurrentTimeText.Text = FormatTime(currentTime);
                    DurationText.Text = FormatTime(duration);
                }
                PlayBtn.Content = isPaused ? "\uE768" : "\uE769";
                if (!sliderHeld && (DateTime.Now - lastUserSeek).TotalMilliseconds > 1200)
                {
                    sliderSync = true;
                    try
                    {
                        double maxValue = Math.Max(1, duration);
                        double position = duration > 0 ? Math.Max(0, Math.Min(currentTime, duration)) : Math.Max(0, currentTime);
                        SeekSlider.Maximum = maxValue;
                        SeekSlider.Value = position;
                    }
                    finally
                    {
                        sliderSync = false;
                    }
                    UpdateSeekThumb();
                }

                // Кеш/стрим + громкость (volume_boost на сервере хранится строкой)
                if (obj.ContainsKey("config"))
                {
                    try
                    {
                        var cfg = obj.GetNamedObject("config");
                        cacheEnabled = cfg.ContainsKey("cache_enabled") && cfg.GetNamedBoolean("cache_enabled", false);
                        if (cfg.ContainsKey("volume_boost"))
                        {
                            double boost = 1.0;
                            var v = cfg.GetNamedValue("volume_boost");
                            if (v.ValueType == Windows.Data.Json.JsonValueType.Number)
                                boost = v.GetNumber();
                            else if (v.ValueType == Windows.Data.Json.JsonValueType.String)
                                double.TryParse(v.GetString().Replace(',', '.'), out boost);
                            int pct = (int)Math.Round(boost * 100.0 / 5.0) * 5; // кратно шагу слайдера
                            if (!volumeHeld && Math.Abs(VolumeSlider.Value - pct) > 1)
                            {
                                lastServerVolumePct = pct;
                                VolumeSlider.Value = pct;
                            }
                        }
                    }
                    catch { }
                }
                if (obj.ContainsKey("cache_mb"))
                {
                    double mb = obj.GetNamedNumber("cache_mb", 0);
                    CacheText.Text = cacheEnabled ? "Cache: " + mb + " MB" : "Стрим без записи на диск";
                }

                string profileName = obj.ContainsKey("active_profile") ? obj.GetNamedString("active_profile", "") : "";
                bool authed = obj.ContainsKey("is_auth") && obj.GetNamedBoolean("is_auth", false);
                string accountName = profileName;
                if (obj.ContainsKey("account_info") && obj.GetNamedValue("account_info").ValueType == Windows.Data.Json.JsonValueType.Object)
                {
                    try
                    {
                        var acc = obj.GetNamedObject("account_info");
                        string accName = acc.GetNamedString("accountName", "");
                        if (!string.IsNullOrEmpty(accName))
                        {
                            accountName = accName;
                        }
                    }
                    catch
                    {
                    }
                }
                AccountText.Text = (authed ? "● " : "○ ") + (string.IsNullOrEmpty(accountName) ? "Гость" : accountName);

                pollFails = 0;
            }
            catch
            {
                pollFails++;
                if (pollFails >= 2)
                {
                    TitleText.Text = "Сервер не запущен";
                    ArtistText.Text = "Запусти start_radio.bat";
                }
            }
        }

        private async void Timer_Tick(object sender, object e)
        {
            await RefreshStatusAsync();
        }

        // ---------- ПОЛНАЯ ОЧЕРЕДЬ ----------

        private void UpdateQueue(Windows.Data.Json.JsonArray queue)
        {
            int count = queue == null ? 0 : (int)queue.Count;
            QueueCountText.Text = count + " в очереди";
            QueueEmptyText.Visibility = count == 0 ? Visibility.Visible : Visibility.Collapsed;

            // Пересборка каждый тик сбрасывает скролл и теряет тапы —
            // перерисовываем только при изменении содержимого
            var sig = new System.Text.StringBuilder();
            if (queue != null)
            {
                for (uint i = 0; i < queue.Count; i++)
                {
                    var it = queue.GetObjectAt(i);
                    sig.Append(it.GetNamedString("videoId", "")).Append('|');
                    sig.Append(it.GetNamedString("title", "")).Append('|');
                    sig.Append(it.GetNamedString("artist", "")).Append(';');
                }
            }
            string sigStr = sig.ToString();
            if (sigStr == _lastQueueSig) return;
            _lastQueueSig = sigStr;

            QueueListPanel.Children.Clear();
            if (queue == null) return;

            for (uint i = 0; i < queue.Count; i++)
            {
                var item = queue.GetObjectAt((uint)i);
                string videoId = item.GetNamedString("videoId", "");
                string title = item.GetNamedString("title", "Трек");
                string artist = item.GetNamedString("artist", "");
                QueueListPanel.Children.Add(BuildListRow(
                    (i + 1).ToString(),
                    string.IsNullOrWhiteSpace(title) ? "Без названия" : title,
                    string.IsNullOrWhiteSpace(artist) ? "" : artist,
                    OnQueueRowPlay, OnQueueRowRemove, videoId, (int)i));
            }
        }

        private void OnQueueRowPlay(string videoId, object tag)
        {
            ShowNotice("Переключаю трек...");
            _ = PostControlAsync("skip_to", videoId: videoId);
        }

        private void OnQueueRowRemove(string videoId, object tag)
        {
            ShowNotice("Убираю из очереди...");
            _ = PostQueueRemoveAsync(false, videoId);
        }

        // ---------- РАДИОСТАНЦИИ ----------

        private async System.Threading.Tasks.Task RefreshStationsAsync()
        {
            try
            {
                RadioHealthText.Text = "проверка потоков...";
                var resp = await http.GetAsync(new Uri(Api + "/api/stations?health=1"));
                var json = await resp.Content.ReadAsStringAsync();
                var obj = Windows.Data.Json.JsonObject.Parse(json);
                if (!obj.GetNamedBoolean("ok", false)) return;

                var stations = obj.GetNamedArray("stations");
                var health = obj.ContainsKey("health") ? obj.GetNamedObject("health") : null;

                StationsListPanel.Children.Clear();
                if (obj.ContainsKey("active"))
                    activeStationId = obj.GetNamedString("active", "");
                int aliveCount = 0, total = 0;
                for (uint i = 0; i < stations.Count; i++)
                {
                    var st = stations.GetObjectAt((uint)i);
                    string id = st.GetNamedString("id", "");
                    string name = st.GetNamedString("name", "Станция");
                    string genre = st.GetNamedString("genre", "");

                    bool alive = true;
                    if (health != null && health.ContainsKey(id))
                    {
                        alive = health.GetNamedBoolean(id, true);
                    }
                    if (alive) aliveCount++;
                    total++;

                    StationsListPanel.Children.Add(BuildStationRow(id, name, genre, alive));
                }
                RadioHealthText.Text = aliveCount + "/" + total + " работают";
            }
            catch
            {
                RadioHealthText.Text = "недоступно";
            }
        }

        private UIElement BuildStationRow(string id, string name, string genre, bool alive)
        {
            var grid = new Grid { Padding = new Thickness(7, 4, 7, 4), CornerRadius = new CornerRadius(8), Margin = new Thickness(0, 3, 0, 0) };
            grid.Background = new SolidColorBrush(Windows.UI.ColorHelper.FromArgb(255, 24, 24, 24));
            if (id == activeStationId)
            {
                grid.BorderBrush = new SolidColorBrush(Windows.UI.ColorHelper.FromArgb(255, 29, 185, 84));
                grid.BorderThickness = new Thickness(1);
            }

            // UWP IVector не имеет AddRange — добавляем по одному
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });

            var info = new StackPanel { Opacity = alive ? 1.0 : 0.45 };
            info.Children.Add(new TextBlock
            {
                Text = name + (id == activeStationId ? "  •" : ""),
                FontSize = 11,
                FontWeight = Windows.UI.Text.FontWeights.SemiBold,
                Foreground = new SolidColorBrush(id == activeStationId
                    ? Windows.UI.ColorHelper.FromArgb(255, 29, 185, 84)
                    : Windows.UI.ColorHelper.FromArgb(255, 255, 255, 255)),
                TextTrimming = TextTrimming.CharacterEllipsis
            });
            info.Children.Add(new TextBlock
            {
                Text = (alive ? genre : genre + " — недоступна"),
                FontSize = 9,
                Foreground = new SolidColorBrush(Windows.UI.ColorHelper.FromArgb(255, 122, 127, 138)),
                TextTrimming = TextTrimming.CharacterEllipsis
            });
            info.Children[1].Opacity = alive ? 1.0 : 0.9;
            Grid.SetColumn(info, 0);

            var playBtn = new Button
            {
                Content = "\uE768",
                FontFamily = new FontFamily("Segoe MDL2 Assets"),
                Width = 30,
                Height = 28,
                Padding = new Thickness(0),
                Background = new SolidColorBrush(Windows.UI.ColorHelper.FromArgb(255, 29, 185, 84)),
                Foreground = new SolidColorBrush(Windows.UI.ColorHelper.FromArgb(255, 0, 0, 0)),
                BorderThickness = new Thickness(0),
                FontSize = 11,
                IsEnabled = alive
            };
            var capturedId = id;
            playBtn.Click += (s, e) =>
            {
                ShowNotice("Включаю радио...");
                _ = PlayStationAsync(capturedId);
            };
            Grid.SetColumn(playBtn, 1);

            grid.Children.Add(info);
            grid.Children.Add(playBtn);
            grid.Tapped += (s, e) =>
            {
                ShowNotice("Включаю радио...");
                _ = PlayStationAsync(capturedId);
            };
            return grid;
        }

        private async System.Threading.Tasks.Task PlayStationAsync(string id)
        {
            try
            {
                var body = new Windows.Data.Json.JsonObject();
                body["id"] = Windows.Data.Json.JsonValue.CreateStringValue(id);
                var content = new Windows.Web.Http.HttpStringContent(
                    body.Stringify(), Windows.Storage.Streams.UnicodeEncoding.Utf8, "application/json");
                var response = await http.PostAsync(new Uri(Api + "/api/station/play"), content);
                if (!response.IsSuccessStatusCode) throw new InvalidOperationException("station failed");
                await RefreshStatusAsync();
                await RefreshStationsAsync();
            }
            catch
            {
                ShowNotice("Ошибка станции");
            }
        }

        private async void StopStation_Click(object sender, RoutedEventArgs e)
        {
            ShowNotice("Стоп радио — вернусь к очереди");
            try
            {
                await http.PostAsync(new Uri(Api + "/api/station/stop"), new Windows.Web.Http.HttpStringContent("{}",
                    Windows.Storage.Streams.UnicodeEncoding.Utf8, "application/json"));
                await RefreshStatusAsync();
                await RefreshStationsAsync();
            }
            catch
            {
                ShowNotice("Ошибка");
            }
        }

        // ---------- ПОИСК ----------

        private async void SearchNow_Click(object sender, RoutedEventArgs e)
        {
            await DoSearchAsync();
        }

        private async void SearchBox_KeyDown(object sender, KeyRoutedEventArgs e)
        {
            if (e.Key == Windows.System.VirtualKey.Enter)
            {
                e.Handled = true;
                // Enter в поле — запуск как есть (ссылка или название)
                string query = SearchBox.Text.Trim();
                if (!string.IsNullOrEmpty(query))
                {
                    ShowNotice("Запуск: " + query + "...");
                    SearchStatusText.Text = "";
                    await PostChangeAsync(query, "");
                    SearchBox.Text = "";
                }
            }
        }

        private async System.Threading.Tasks.Task DoSearchAsync()
        {
            string q = SearchBox.Text.Trim();
            if (string.IsNullOrEmpty(q)) return;
            SearchStatusText.Text = "Ищу...";
            SearchResultsPanel.Children.Clear();
            try
            {
                var resp = await http.GetAsync(new Uri(Api + "/api/search?q=" + Uri.EscapeDataString(q)));
                var json = await resp.Content.ReadAsStringAsync();
                var obj = Windows.Data.Json.JsonObject.Parse(json);
                if (!obj.GetNamedBoolean("ok", false)) throw new InvalidOperationException(obj.GetNamedString("error", "Ошибка поиска"));
                var results = obj.GetNamedArray("results");
                SearchStatusText.Text = results.Count == 0 ? "Ничего не найдено" : "Найдено: " + results.Count + " — тап по треку запускает";
                for (uint i = 0; i < results.Count; i++)
                {
                    var r = results.GetObjectAt((uint)i);
                    var tr = new TrackResult
                    {
                        VideoId = r.GetNamedString("videoId", ""),
                        Title = r.GetNamedString("title", "Трек"),
                        Artist = r.GetNamedString("artist", ""),
                        Thumbnail = r.GetNamedString("thumbnail", "")
                    };
                    SearchResultsPanel.Children.Add(BuildSearchRow(tr));
                }
            }
            catch (Exception ex)
            {
                SearchStatusText.Text = "Ошибка поиска: " + ex.Message;
            }
        }

        private UIElement BuildSearchRow(TrackResult tr)
        {
            var grid = new Grid { Padding = new Thickness(7, 4, 7, 4), CornerRadius = new CornerRadius(8), Margin = new Thickness(0, 3, 0, 0) };
            grid.Background = new SolidColorBrush(Windows.UI.ColorHelper.FromArgb(255, 24, 24, 24));

            // UWP IVector не имеет AddRange — добавляем по одному
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });

            var info = new StackPanel();
            info.Children.Add(new TextBlock
            {
                Text = string.IsNullOrWhiteSpace(tr.Title) ? "Без названия" : tr.Title,
                FontSize = 11,
                FontWeight = Windows.UI.Text.FontWeights.SemiBold,
                Foreground = new SolidColorBrush(Windows.UI.ColorHelper.FromArgb(255, 255, 255, 255)),
                TextTrimming = TextTrimming.CharacterEllipsis
            });
            info.Children.Add(new TextBlock
            {
                Text = string.IsNullOrWhiteSpace(tr.Artist) ? "YouTube Music" : tr.Artist,
                FontSize = 9,
                Foreground = new SolidColorBrush(Windows.UI.ColorHelper.FromArgb(255, 122, 127, 138)),
                TextTrimming = TextTrimming.CharacterEllipsis
            });
            Grid.SetColumn(info, 0);

            var playBtn = new Button
            {
                Content = "\uE768",
                FontFamily = new FontFamily("Segoe MDL2 Assets"),
                Width = 30,
                Height = 28,
                Padding = new Thickness(0),
                Background = new SolidColorBrush(Windows.UI.ColorHelper.FromArgb(255, 29, 185, 84)),
                Foreground = new SolidColorBrush(Windows.UI.ColorHelper.FromArgb(255, 0, 0, 0)),
                BorderThickness = new Thickness(0),
                FontSize = 11
            };
            playBtn.Click += (s, e) =>
            {
                ShowNotice("Запуск: " + tr.Title + "...");
                _ = PostChangeWithMetaAsync(tr);
            };
            Grid.SetColumn(playBtn, 1);

            grid.Children.Add(info);
            grid.Children.Add(playBtn);
            grid.Tapped += (s, e) =>
            {
                ShowNotice("Запуск: " + tr.Title + "...");
                _ = PostChangeWithMetaAsync(tr);
            };
            return grid;
        }

        private async System.Threading.Tasks.Task PostChangeWithMetaAsync(TrackResult tr)
        {
            try
            {
                var body = new Windows.Data.Json.JsonObject();
                body["query"] = Windows.Data.Json.JsonValue.CreateStringValue(tr.VideoId);
                body["offset"] = Windows.Data.Json.JsonValue.CreateNumberValue(0);
                body["title"] = Windows.Data.Json.JsonValue.CreateStringValue(tr.Title ?? "");
                body["artist"] = Windows.Data.Json.JsonValue.CreateStringValue(tr.Artist ?? "");
                body["thumbnail"] = Windows.Data.Json.JsonValue.CreateStringValue(tr.Thumbnail ?? "");
                var content = new Windows.Web.Http.HttpStringContent(
                    body.Stringify(), Windows.Storage.Streams.UnicodeEncoding.Utf8, "application/json");
                var response = await http.PostAsync(new Uri(Api + "/api/change"), content);
                if (!response.IsSuccessStatusCode) throw new InvalidOperationException("change failed");
                await RefreshStatusAsync();
            }
            catch
            {
                ShowNotice("Ошибка запуска");
            }
        }

        // ---------- ПЛЕЙЛИСТЫ ----------

        private async System.Threading.Tasks.Task RefreshPlaylistsAsync()
        {
            try
            {
                var resp = await http.GetAsync(new Uri(Api + "/api/user_playlists"));
                var json = await resp.Content.ReadAsStringAsync();
                var obj = Windows.Data.Json.JsonObject.Parse(json);
                if (obj.ContainsKey("playlists"))
                {
                    var arr = obj.GetNamedArray("playlists");
                    var currentSel = PlaylistCombo.SelectedItem as PlaylistItem;
                    var list = new List<PlaylistItem>();
                    for (uint i = 0; i < arr.Count; i++)
                    {
                        var p = arr.GetObjectAt(i);
                        list.Add(new PlaylistItem
                        {
                            Id = p.GetNamedString("id", ""),
                            Title = p.GetNamedString("title", "")
                        });
                    }

                    PlaylistCombo.Items.Clear();
                    int selIdx = -1;
                    for (int i = 0; i < list.Count; i++)
                    {
                        PlaylistCombo.Items.Add(list[i]);
                        if (currentSel != null && list[i].Id == currentSel.Id)
                        {
                            selIdx = i;
                        }
                    }
                    if (selIdx >= 0)
                    {
                        PlaylistCombo.SelectedIndex = selIdx;
                    }
                    else if (PlaylistCombo.Items.Count > 0 && PlaylistCombo.SelectedIndex < 0)
                    {
                        PlaylistCombo.SelectedIndex = 0;
                    }
                }
            }
            catch { }
        }

        // ---------- SEEK / ГРОМКОСТЬ ----------

        private void SendPendingSeek()
        {
            if (pendingSeek < 0)
            {
                return;
            }
            int target = pendingSeek;
            pendingSeek = -1;
            lastSeekSent = DateTime.Now;
            _ = PostControlAsync("seek", target);
        }

        private void SeekSlider_ValueChanged(object sender, Windows.UI.Xaml.Controls.Primitives.RangeBaseValueChangedEventArgs e)
        {
            if (sliderSync)
            {
                return;
            }
            if (trackIsStation)
            {
                return; // seek на живой станции бессмыслен
            }
            pendingSeek = Math.Max(0, (int)SeekSlider.Value);
            UpdateSeekThumb();
            lastUserSeek = DateTime.Now;
            currentTime = pendingSeek;
            CurrentTimeText.Text = FormatTime(currentTime);
            DurationText.Text = FormatTime(duration);
            if ((DateTime.Now - lastSeekSent).TotalMilliseconds > 900)
            {
                SendPendingSeek();
            }
        }

        private void SeekSlider_PointerPressed(object sender, PointerRoutedEventArgs e)
        {
            sliderHeld = true;
        }

        private void SeekSlider_PointerCaptureLost(object sender, PointerRoutedEventArgs e)
        {
            sliderHeld = false;
            lastUserSeek = DateTime.Now;
            SendPendingSeek();
        }

        private void SeekVisual_SizeChanged(object sender, SizeChangedEventArgs e)
        {
            UpdateSeekThumb();
        }

        private void UpdateSeekThumb()
        {
            if (SeekFill == null || SeekThumb == null || SeekSlider == null)
            {
                return;
            }
            double width = SeekVisual.ActualWidth;
            if (width <= 0)
            {
                return;
            }
            double ratio = SeekSlider.Maximum <= 0 ? 0 : SeekSlider.Value / SeekSlider.Maximum;
            ratio = Math.Max(0, Math.Min(1, ratio));
            SeekFill.Width = ratio * width;
            double thumbWidth = SeekThumb.Width;
            SeekThumb.Margin = new Thickness(Math.Max(0, ratio * width - thumbWidth / 2), 0, 0, 0);
        }

        private void UpdateVolumeVisual()
        {
            if (VolumeFill == null || VolumeThumb == null || VolumeVisual == null)
            {
                return;
            }
            double width = VolumeVisual.ActualWidth;
            if (width <= 0)
            {
                return;
            }
            double ratio = (VolumeSlider.Value - VolumeSlider.Minimum) / Math.Max(1, VolumeSlider.Maximum - VolumeSlider.Minimum);
            ratio = Math.Max(0, Math.Min(1, ratio));
            VolumeFill.Width = ratio * width;
            double thumbWidth = VolumeThumb.Width;
            VolumeThumb.Margin = new Thickness(Math.Max(0, ratio * width - thumbWidth / 2), 0, 0, 0);
        }

        private void VolumeVisual_SizeChanged(object sender, SizeChangedEventArgs e)
        {
            UpdateVolumeVisual();
        }

        private void VolumeSlider_PointerPressed(object sender, PointerRoutedEventArgs e)
        {
            volumeHeld = true;
        }

        private void VolumeSlider_PointerCaptureLost(object sender, PointerRoutedEventArgs e)
        {
            volumeHeld = false;
            SendPendingVolume();
        }

        private void VolumeSlider_ValueChanged(object sender, Windows.UI.Xaml.Controls.Primitives.RangeBaseValueChangedEventArgs e)
        {
            if (VolumeLabel == null) return;
            int pct = (int)VolumeSlider.Value;
            VolumeLabel.Text = pct + "%";
            UpdateVolumeVisual();
            // не отправляем на сервер, пока значение синхронизируется из конфига
            if (Math.Abs(pct - lastServerVolumePct) < 1)
            {
                return;
            }
            pendingVolume = pct;
            if ((DateTime.Now - lastVolumeSent).TotalMilliseconds > 400)
            {
                SendPendingVolume();
            }
        }

        private async void SendPendingVolume()
        {
            if (pendingVolume < 0) return;
            int pct = pendingVolume;
            pendingVolume = -1;
            lastVolumeSent = DateTime.Now;
            lastServerVolumePct = pct;
            try
            {
                var body = new Windows.Data.Json.JsonObject();
                body["volume_boost"] = Windows.Data.Json.JsonValue.CreateNumberValue(pct / 100.0);
                var content = new Windows.Web.Http.HttpStringContent(
                    body.Stringify(), Windows.Storage.Streams.UnicodeEncoding.Utf8, "application/json");
                await http.PostAsync(new Uri(Api + "/api/config"), content);
            }
            catch
            {
            }
        }

        private static string FormatTime(int sec)
        {
            if (sec < 0) sec = 0;
            return (sec / 60) + ":" + (sec % 60).ToString("D2");
        }

        // ---------- ТРАНСПОРТ ----------

        private async void PlayPause_Click(object sender, RoutedEventArgs e)
        {
            await PostControlAsync("pause");
        }

        private async void Like_Click(object sender, RoutedEventArgs e)
        {
            await PostAccountLikeAsync();
        }

        private async System.Threading.Tasks.Task PostAccountLikeAsync()
        {
            if (string.IsNullOrEmpty(currentVideoId) || currentVideoId.StartsWith("station:"))
            {
                return;
            }
            try
            {
                var body = new Windows.Data.Json.JsonObject();
                body["video_id"] = Windows.Data.Json.JsonValue.CreateStringValue(currentVideoId);
                body["title"] = Windows.Data.Json.JsonValue.CreateStringValue(currentTitle);
                body["artist"] = Windows.Data.Json.JsonValue.CreateStringValue(currentArtist);
                body["thumbnail"] = Windows.Data.Json.JsonValue.CreateStringValue(currentThumbnail);
                body["album"] = Windows.Data.Json.JsonValue.CreateStringValue(currentAlbum);
                var content = new Windows.Web.Http.HttpStringContent(
                    body.Stringify(), Windows.Storage.Streams.UnicodeEncoding.Utf8, "application/json");
                var response = await http.PostAsync(new Uri(Api + "/api/like"), content);
                var payload = await response.Content.ReadAsStringAsync();
                if (!response.IsSuccessStatusCode) throw new InvalidOperationException("Like failed");
                var obj = Windows.Data.Json.JsonObject.Parse(payload);
                if (!obj.GetNamedBoolean("ok", false)) throw new InvalidOperationException(obj.GetNamedString("error", "Like failed"));
                bool liked = obj.GetNamedBoolean("liked", false);
                bool inAccount = obj.GetNamedBoolean("account", false);
                string msg = obj.GetNamedString("message", "");
                LikeBtn.Content = liked ? "\uE735" : "\uE734";
                ToolTipService.SetToolTip(LikeBtn, liked ? "Убрать лайк" : "Лайк в аккаунт");
                if (liked)
                {
                    ShowNotice(inAccount ? "Лайк в аккаунте" : "Только локально: " + (string.IsNullOrEmpty(msg) ? "аккаунт недоступен" : msg));
                }
                else
                {
                    ShowNotice("Лайк убран");
                }
                await RefreshStatusAsync();
            }
            catch
            {
                ShowNotice("Ошибка лайка");
            }
        }

        private async void Next_Click(object sender, RoutedEventArgs e)
        {
            await PostControlAsync("skip");
        }

        private async void Dislike_Click(object sender, RoutedEventArgs e)
        {
            ShowNotice("Убираю трек, подбираю замену...");
            await PostQueueRemoveAsync(true);
        }

        private async void Prev_Click(object sender, RoutedEventArgs e)
        {
            await PostControlAsync("previous");
        }

        private async void Rewind_Click(object sender, RoutedEventArgs e)
        {
            await PostControlAsync("seek", Math.Max(0, currentTime - 15));
        }

        private async void Forward_Click(object sender, RoutedEventArgs e)
        {
            int target = duration > 0 ? Math.Min(duration, currentTime + 15) : currentTime + 15;
            await PostControlAsync("seek", target);
        }

        private async void PlayPlaylist_Click(object sender, RoutedEventArgs e)
        {
            if (PlaylistCombo.SelectedItem is PlaylistItem item && !string.IsNullOrEmpty(item.Id))
            {
                ShowNotice("Загрузка " + item.Title + "...");
                await PostChangeAsync(item.Id, "");
            }
        }

        private async void PlayLikes_Click(object sender, RoutedEventArgs e)
        {
            ShowNotice("Загрузка Лайков...");
            await PostChangeAsync("LM", "");
        }

        private async void PlayRemix_Click(object sender, RoutedEventArgs e)
        {
            var item = PlaylistCombo.Items
                .OfType<PlaylistItem>()
                .FirstOrDefault(x => x.Title.IndexOf("Remix", StringComparison.OrdinalIgnoreCase) >= 0);
            if (item == null)
            {
                ShowNotice("Плейлист Remix не найден");
                return;
            }
            ShowNotice("Загрузка Remix tracks...");
            await PostChangeAsync(item.Id, "");
        }

        private void OpenUI_Click(object sender, RoutedEventArgs e)
        {
            _ = Windows.System.Launcher.LaunchUriAsync(new Uri("http://127.0.0.1:5000/"));
        }

        // ---------- ВКЛАДКИ ----------

        private void TabPlayer_Click(object sender, RoutedEventArgs e) => ShowTab("player");
        private void TabQueue_Click(object sender, RoutedEventArgs e) => ShowTab("queue");
        private void TabRadio_Click(object sender, RoutedEventArgs e) => ShowTab("radio");
        private void TabSearch_Click(object sender, RoutedEventArgs e) => ShowTab("search");

        // ---------- HTTP ----------

        private async System.Threading.Tasks.Task PostControlAsync(string action, int? time = null, string videoId = null)
        {
            try
            {
                var body = new Windows.Data.Json.JsonObject();
                body["action"] = Windows.Data.Json.JsonValue.CreateStringValue(action);
                if (time.HasValue) body["time"] = Windows.Data.Json.JsonValue.CreateNumberValue(time.Value);
                if (!string.IsNullOrEmpty(videoId)) body["video_id"] = Windows.Data.Json.JsonValue.CreateStringValue(videoId);
                var content = new Windows.Web.Http.HttpStringContent(
                    body.Stringify(), Windows.Storage.Streams.UnicodeEncoding.Utf8, "application/json");
                var response = await http.PostAsync(new Uri(Api + "/api/control"), content);
                if (!response.IsSuccessStatusCode) throw new InvalidOperationException("Control failed");
                await RefreshStatusAsync();
            }
            catch
            {
                ShowNotice("Ошибка управления");
            }
        }

        private async System.Threading.Tasks.Task PostQueueRemoveAsync(bool current, string videoId = null)
        {
            try
            {
                var body = new Windows.Data.Json.JsonObject();
                body["current"] = Windows.Data.Json.JsonValue.CreateBooleanValue(current);
                if (!string.IsNullOrEmpty(videoId)) body["video_id"] = Windows.Data.Json.JsonValue.CreateStringValue(videoId);
                var content = new Windows.Web.Http.HttpStringContent(
                    body.Stringify(), Windows.Storage.Streams.UnicodeEncoding.Utf8, "application/json");
                var response = await http.PostAsync(new Uri(Api + "/api/queue/remove"), content);
                if (!response.IsSuccessStatusCode) throw new InvalidOperationException("Queue update failed");
                await RefreshStatusAsync();
            }
            catch
            {
                ShowNotice("Ошибка удаления трека");
            }
        }

        private async System.Threading.Tasks.Task PostChangeAsync(string query, string extra)
        {
            try
            {
                var body = new Windows.Data.Json.JsonObject();
                body["query"] = Windows.Data.Json.JsonValue.CreateStringValue(query);
                body["offset"] = Windows.Data.Json.JsonValue.CreateNumberValue(0);
                var content = new Windows.Web.Http.HttpStringContent(
                    body.Stringify(), Windows.Storage.Streams.UnicodeEncoding.Utf8, "application/json");
                var response = await http.PostAsync(new Uri(Api + "/api/change"), content);
                if (!response.IsSuccessStatusCode) throw new InvalidOperationException("Change failed");
                await RefreshStatusAsync();
            }
            catch
            {
                ShowNotice("Ошибка запуска");
            }
        }

        // ---------- ДИНАМИЧЕСКИЕ СТРОКИ СПИСКОВ ----------

        private UIElement BuildListRow(string num, string title, string artist,
            Action<string, object> onPlay, Action<string, object> onRemove, string videoId, int index)
        {
            var grid = new Grid { Padding = new Thickness(7, 4, 7, 4), CornerRadius = new CornerRadius(8), Margin = new Thickness(0, 3, 0, 0) };
            grid.Background = new SolidColorBrush(Windows.UI.ColorHelper.FromArgb(255, 24, 24, 24));

            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });

            var numBox = new TextBlock
            {
                Text = num,
                FontSize = 10,
                FontFamily = new FontFamily("Consolas"),
                Foreground = new SolidColorBrush(Windows.UI.ColorHelper.FromArgb(255, 122, 127, 138)),
                VerticalAlignment = VerticalAlignment.Center,
                MinWidth = 18
            };

            var info = new StackPanel { Margin = new Thickness(6, 0, 2, 0) };
            info.Children.Add(new TextBlock
            {
                Text = title,
                FontSize = 11,
                FontWeight = Windows.UI.Text.FontWeights.SemiBold,
                Foreground = new SolidColorBrush(Windows.UI.ColorHelper.FromArgb(255, 255, 255, 255)),
                TextTrimming = TextTrimming.CharacterEllipsis
            });
            info.Children.Add(new TextBlock
            {
                Text = artist,
                FontSize = 9,
                Foreground = new SolidColorBrush(Windows.UI.ColorHelper.FromArgb(255, 122, 127, 138)),
                TextTrimming = TextTrimming.CharacterEllipsis
            });
            Grid.SetColumn(info, 1);

            var actions = new StackPanel { Orientation = Orientation.Horizontal };
            var playBtn = new Button
            {
                Content = "\uE768",
                FontFamily = new FontFamily("Segoe MDL2 Assets"),
                Width = 28,
                Height = 26,
                Padding = new Thickness(0),
                Margin = new Thickness(0, 0, 3, 0),
                Background = new SolidColorBrush(Windows.UI.ColorHelper.FromArgb(255, 29, 185, 84)),
                Foreground = new SolidColorBrush(Windows.UI.ColorHelper.FromArgb(255, 0, 0, 0)),
                BorderThickness = new Thickness(0),
                FontSize = 11
            };
            playBtn.Click += (s, e) => onPlay?.Invoke(videoId, index);
            var removeBtn = new Button
            {
                Content = "\uE711",
                FontFamily = new FontFamily("Segoe MDL2 Assets"),
                Width = 24,
                Height = 26,
                Padding = new Thickness(0),
                Background = new SolidColorBrush(Windows.UI.ColorHelper.FromArgb(255, 60, 30, 34)),
                Foreground = new SolidColorBrush(Windows.UI.ColorHelper.FromArgb(255, 255, 111, 111)),
                BorderThickness = new Thickness(0),
                FontSize = 11
            };
            removeBtn.Click += (s, e) => onRemove?.Invoke(videoId, index);
            actions.Children.Add(playBtn);
            actions.Children.Add(removeBtn);
            Grid.SetColumn(actions, 2);

            grid.Children.Add(numBox);
            grid.Children.Add(info);
            grid.Children.Add(actions);
            return grid;
        }
    }
}
