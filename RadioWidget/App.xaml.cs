using System;
using System.IO;
using Windows.ApplicationModel;
using Windows.ApplicationModel.Activation;
using Windows.Storage;
using Windows.UI.Xaml;
using Windows.UI.Xaml.Controls;
using Windows.UI.Xaml.Navigation;
using Microsoft.Gaming.XboxGameBar;

namespace RadioWidget
{
    sealed partial class App : Application
    {
        public static XboxGameBarWidget Widget { get; private set; }

        public App()
        {
            this.UnhandledException += (sender, args) =>
            {
                LogException(args.Exception);
                args.Handled = true;
            };
            this.InitializeComponent();
            this.Suspending += OnSuspending;
        }

        private static void LogException(Exception exception)
        {
            try
            {
                var path = Path.Combine(ApplicationData.Current.LocalFolder.Path, "widget_crash.log");
                File.AppendAllText(path, exception + Environment.NewLine);
            }
            catch
            {
            }
        }

        protected override void OnActivated(IActivatedEventArgs args)
        {
            XboxGameBarWidgetActivatedEventArgs widgetArgs = null;
            if (args.Kind == ActivationKind.Protocol)
            {
                var protocolArgs = args as IProtocolActivatedEventArgs;
                if (protocolArgs != null && protocolArgs.Uri != null && protocolArgs.Uri.Scheme.Equals("ms-gamebarwidget"))
                {
                    widgetArgs = args as XboxGameBarWidgetActivatedEventArgs;
                }
            }
            if (widgetArgs != null)
            {
                if (widgetArgs.IsLaunchActivation)
                {
                    var rootFrame = new Frame();
                    rootFrame.NavigationFailed += OnNavigationFailed;
                    Window.Current.Content = rootFrame;

                    Widget = new XboxGameBarWidget(
                        widgetArgs,
                        Window.Current.CoreWindow,
                        rootFrame);
                    rootFrame.Navigate(typeof(RadioPage));

                    Window.Current.Closed += RadioWindow_Closed;
                    Window.Current.Activate();
                }
            }
        }

        private void RadioWindow_Closed(object sender, Windows.UI.Core.CoreWindowEventArgs e)
        {
            Window.Current.Closed -= RadioWindow_Closed;
        }

        protected override void OnLaunched(LaunchActivatedEventArgs e)
        {
            try
            {
                Frame rootFrame = Window.Current.Content as Frame;
                if (rootFrame == null)
                {
                    rootFrame = new Frame();
                    rootFrame.NavigationFailed += OnNavigationFailed;
                    Window.Current.Content = rootFrame;
                }
                if (e.PrelaunchActivated == false)
                {
                    if (rootFrame.Content == null)
                    {
                        rootFrame.Navigate(typeof(RadioPage), e.Arguments);
                    }
                    Window.Current.Activate();
                }
            }
            catch (Exception exception)
            {
                LogException(exception);
            }
        }

        void OnNavigationFailed(object sender, NavigationFailedEventArgs e)
        {
            throw new Exception("Failed to load Page " + e.SourcePageType.FullName);
        }

        private void OnSuspending(object sender, SuspendingEventArgs e)
        {
            var deferral = e.SuspendingOperation.GetDeferral();
            deferral.Complete();
        }
    }
}
