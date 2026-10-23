Name:           lifeboat
# x-release-please-start-version
Version:        0.3.2
# x-release-please-end
Release:        1%{?dist}
Summary:        Back up a Linux laptop to Google Drive (or any rclone remote) and see if it is safe to lose

License:        MIT
URL:            https://github.com/xixas/lifeboat
Source0:        %{url}/releases/download/v%{version}/lifeboat-%{version}.tar.gz

BuildArch:      noarch
BuildRequires:  python3-devel
BuildRequires:  desktop-file-utils

Requires:       python3-gobject
Requires:       gtk4
Requires:       libadwaita >= 1.5
Requires:       rclone >= 1.60
Requires:       hicolor-icon-theme

%description
Lifeboat wraps rclone to back up a Linux laptop to Google Drive or any other
rclone remote, shows whether everything is safely backed up, and lets you
restore it, from a GTK 4 / libadwaita app or the command line.

%prep
%autosetup -n lifeboat-%{version}

%generate_buildrequires
%pyproject_buildrequires

%build
%pyproject_wheel

%install
%pyproject_install
%pyproject_save_files lifeboat

desktop-file-install --dir=%{buildroot}%{_datadir}/applications \
    data/io.github.xixas.Lifeboat.desktop
install -Dpm 0644 data/io.github.xixas.Lifeboat.svg \
    %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/io.github.xixas.Lifeboat.svg
install -Dpm 0644 data/config.example.toml \
    %{buildroot}%{_datadir}/lifeboat/config.example.toml
install -d %{buildroot}%{_datadir}/dbus-1/services
sed 's|@BIN@|%{_bindir}/lifeboat|' data/io.github.xixas.Lifeboat.service.in \
    > %{buildroot}%{_datadir}/dbus-1/services/io.github.xixas.Lifeboat.service

%check
# lifeboat.app imports gi (GTK), which is a runtime-only dependency
%pyproject_check_import -e 'lifeboat.app*'
desktop-file-validate %{buildroot}%{_datadir}/applications/io.github.xixas.Lifeboat.desktop

%files -f %{pyproject_files}
%license LICENSE
%doc README.md
%{_bindir}/lifeboat
%{_datadir}/applications/io.github.xixas.Lifeboat.desktop
%{_datadir}/icons/hicolor/scalable/apps/io.github.xixas.Lifeboat.svg
%{_datadir}/dbus-1/services/io.github.xixas.Lifeboat.service
%{_datadir}/lifeboat/

%changelog
* Fri Oct 23 2026 Vikas Kyatannawar <vikas.kyatannawar@gmail.com> - 0.3.2-1
- Initial RPM package
