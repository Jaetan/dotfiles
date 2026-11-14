#!/usr/bin/env bash
# Builds the shell reader into ../bin/shell-reader once gofmt has nothing to change, go vet is clean and the tests
# pass, so a red reader is never installed. The module is read-only (-mod=readonly: go.mod and go.sum are never
# rewritten) and mvdan.cc/sh comes from the caller's GOPROXY, its hash checked against go.sum; the module and build
# caches live beneath the git-ignored bin/go/. Exit non-zero: a file gofmt would change, vet, a test or the build.
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
bin=$(cd "$here/.." && pwd)/bin
mkdir -p "$bin"
export GOFLAGS="-mod=readonly -modcacherw -buildvcs=false" GOTOOLCHAIN=local GOTELEMETRY=off
export GOMODCACHE="${GOMODCACHE:-$bin/go/mod}" GOCACHE="${GOCACHE:-$bin/go/cache}" GOPATH="${GOPATH:-$bin/go/path}"
cd "$here"
unformatted=$("$(go env GOROOT)/bin/gofmt" -l .)
if [[ -n $unformatted ]]; then
    echo "gofmt would change: $unformatted" >&2
    exit 1
fi
go vet ./...
go test -count=1 ./...
go build -trimpath -o "$bin/shell-reader" .
