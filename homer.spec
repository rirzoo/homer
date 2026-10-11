Name:           homer
Version:        0.1.0
Release:        1%{?dist}
Summary:        Multi-account mail CLI and TUI for Gmail/IMAP and Microsoft 365
License:        MIT
URL:            https://github.com/rirzoo/homer
Source0:        %{url}/archive/v%{version}/%{name}-%{version}.tar.gz
# Textual and its dependencies as wheels, downloaded by .copr/Makefile when it builds the SRPM.
# Fedora's python3-textual and python3-rich are too old for the TUI, so they're bundled privately.
Source1:        %{name}-vendor-%{version}.tar.gz
BuildArch:      noarch

BuildRequires:  python3-devel
BuildRequires:  python3-pip
Requires:       python3 >= 3.11
Requires:       libsecret

%description
Homer reads, sends, replies to and organises mail from the terminal, across
Gmail, any IMAP/SMTP server and Microsoft 365 (Outlook). Its plain-text CLI
lets AI agents handle mail for you, and running `homer` on its own opens a
keyboard-driven TUI.

%prep
%autosetup -n %{name}-%{version}
tar -xzf %{SOURCE1}

%build
# Pure Python; nothing to build.

%install
mkdir -p %{buildroot}%{_datadir}/%{name} %{buildroot}%{_bindir}
%{python3} -m pip install --root-user-action=ignore --no-index --find-links vendor --no-compile --no-deps \
    --target %{buildroot}%{_datadir}/%{name} vendor/*.whl
rm -rf %{buildroot}%{_datadir}/%{name}/bin
cp -a homer %{buildroot}%{_datadir}/%{name}/
find %{buildroot}%{_datadir}/%{name} -name __pycache__ -prune -exec rm -rf {} +
cat > %{buildroot}%{_bindir}/%{name} <<'EOF'
#!%{python3} -sP
import sys
sys.path.insert(0, "%{_datadir}/%{name}")
from homer.cli import main
sys.exit(main())
EOF
chmod 0755 %{buildroot}%{_bindir}/%{name}
%py_byte_compile %{python3} %{buildroot}%{_datadir}/%{name}

%check
PYTHONPATH=%{buildroot}%{_datadir}/%{name} %{python3} -P test_homer.py
PYTHONPATH=%{buildroot}%{_datadir}/%{name} %{buildroot}%{_bindir}/%{name} --help > /dev/null

%files
%license LICENSE
%doc README.md TUI.md
%{_bindir}/%{name}
%{_datadir}/%{name}/

%changelog
* Sun Oct 11 2026 Romir Zadoo <romirzadoo1@gmail.com> - 0.1.0-1
- First RPM release
