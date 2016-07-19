#
# RPM Spec for pScheduler Traceroute Tool
#

%define short	traceroute
Name:		pscheduler-tool-%{short}
Version:	0.0
Release:	1%{?dist}

Summary:	pScheduler Traceroute Tool
BuildArch:	noarch
License:	Apache 2.0
Group:		Unspecified

Source0:	%{short}-%{version}.tar.gz

Provides:	%{name} = %{version}-%{release}

Requires:	pscheduler-core
Requires:	pscheduler-account
Requires:	python-pscheduler
Requires:	pscheduler-test-trace
requires:	traceroute
requires:	sudo

BuildRequires:	pscheduler-account
BuildRequires:	pscheduler-rpm
BuildRequires:	traceroute

%description
pScheduler Traceroute Tool


%prep
%setup -q -n %{short}-%{version}


%define dest %{_pscheduler_tool_libexec}/%{short}

%install
make \
     DESTDIR=$RPM_BUILD_ROOT/%{dest} \
     DOCDIR=$RPM_BUILD_ROOT/%{_pscheduler_tool_doc} \
     install


# Enable sudo for traceroute

TRACEROUTE=$(which traceroute)

mkdir -p $RPM_BUILD_ROOT/%{_pscheduler_sudoersdir}
cat > $RPM_BUILD_ROOT/%{_pscheduler_sudoersdir}/%{name} <<EOF
#
# %{name}
#
Cmnd_Alias PSCHEDULER_TOOL_TRACEROUTE = ${TRACEROUTE}
%{_pscheduler_user} ALL = (root) NOPASSWD: ${TRACEROUTE}
Defaults!PSCHEDULER_TOOL_TRACEROUTE !requiretty


EOF

%post
# TODO: Insert iptables rules to allow traceroute out?


%postun
# TODO: Delete iptables rules to allow traceroute out?


%files
%defattr(-,root,root,-)
%{dest}
%attr(440,root,root) %{_pscheduler_sudoersdir}/*