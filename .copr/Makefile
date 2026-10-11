# Copr "make_srpm" entry point: copr runs `make -f .copr/Makefile srpm outdir=... spec=...` in the clone.
# Locally: make -f .copr/Makefile srpm outdir=/tmp/homer-srpm
top := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/..)
version = $(shell sed -n 's/^Version: *//p' $(top)/homer.spec)
outdir ?= $(top)/build/srpm

srpm:
	command -v rpmbuild >/dev/null || dnf -y install git python3-pip rpm-build
	git config --global --add safe.directory '*'
	mkdir -p $(outdir)
	git -C $(top) archive --prefix=homer-$(version)/ -o $(outdir)/homer-$(version).tar.gz HEAD
	rm -rf $(outdir)/vendor
	python3 -m pip download --only-binary=:all: --dest $(outdir)/vendor \
	    $$(python3 -c 'import tomllib; print(*tomllib.load(open("$(top)/pyproject.toml", "rb"))["project"]["dependencies"])')
	tar -czf $(outdir)/homer-vendor-$(version).tar.gz -C $(outdir) vendor
	rpmbuild -bs $(top)/homer.spec --define "_sourcedir $(outdir)" --define "_srcrpmdir $(outdir)"
