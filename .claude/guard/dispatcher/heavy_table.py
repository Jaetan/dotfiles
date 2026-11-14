"""The kit's own heavy rows, read by dispatcher/heavy.py's one matcher as a project's are, every flag and option read
from its tool's --help on this machine, each subcommand's too (pytest 9.2.0, mutmut 3.8.1, pylint 4.1.3, basedpyright
1.40.2, cargo 1.98.1, cargo-mutants 27.1.0, cargo-llvm-cov 0.8.7, go 1.26.3, cmake and ctest 4.1.2, GNU Make 4.4.1,
ninja 1.13.2, cabal-install 3.16.1.0, Agda 2.8.0, gremlins dev, mull-runner-23, bazel 9.3.2, Docker 28.5.2).

A tool runs heavy by its name: pytest, py.test, mutmut, pylint and basedpyright as a program or a module under `python
-m`, cmake, ctest, make, gmake, ninja, agda and mull-runner as a program. A run is light only where it asks for help or
the version (agda's --numeric-version too), where it lists, collects or dry-runs (agda's --print-agda-dir, pytest's
--collect-only and --co, cargo install's --list, cargo mutants' --list, --list-files and --emit-schema, cargo llvm-cov's
--no-run, go test's -list, ctest's -N, --show-only and --print-labels, make's -n and -q, ninja's -n, gremlins unleash's
--dry-run and -d, mull-runner's --dry-run), or where pytest names only files and test ids, pylint and basedpyright only
Python files.

Exceptions, each with the failure it holds:
  - cargo, go, cabal, bazel, docker and gremlins run heavy only under the subcommands their rows name, or `cargo fmt`
    and `go mod tidy` would be refused;
  - the word after an option taking a value is skipped, or `go -C go test` would read as no go test, `basedpyright -p
    x a.py` as naming a folder and `ctest -C -N` as a listing;
  - ctest's -V asks for verbose output, or `ctest -V` would read as a version ask and pass a whole test run;
  - make and gmake's -v is their version, or `make -v` would be refused;
  - mutmut runs heavy only where no subcommand but run is named, or `mutmut results`, `show` and `apply`, which run no
    mutant, would be refused."""

from declarations.schema import Heavy, ModuleName, ProgramName, Word

BARE = Heavy(None, None, (), None, False, (), (), (), None, ())
COLLECT = (Word("--collect-only"), Word("--co"))
MUTMUT_VALUED = (Word("--max-children"),)
AGDA_LIGHT = (Word("--numeric-version"), Word("--print-agda-dir"))
MUTMUT_LIGHT = (Word("apply"), Word("badge"), Word("browse"), Word("export-cicd-stats"), Word("print-time-estimates"),
                Word("results"), Word("show"), Word("tests-for-mutant"))
BASEDPYRIGHT_VALUED = (
    Word("--baselinefile"), Word("--createstub"), Word("--gitlabcodequality"), Word("--level"), Word("--project"),
    Word("--pythonpath"), Word("--pythonplatform"), Word("--pythonversion"), Word("--threads"), Word("--typeshedpath"),
    Word("--venvpath"), Word("--verifytypes"), Word("-p"), Word("-t"), Word("-v"))
CARGO = Word("cargo")
CARGO_ROW = BARE._replace(valued_options=(Word("--explain"), Word("--color"), Word("-C"), Word("--config"), Word("-Z")))
CARGO_WRITERS = (Word("build"), Word("b"), Word("test"), Word("t"), Word("clippy"), Word("bench"), Word("run"), Word("r"),
                 Word("check"), Word("c"), Word("doc"), Word("d"),
                 Word("fix"))
GO = Word("go")
GO_ROW = BARE._replace(valued_options=(Word("-C"),))
GO_BUILDS = (Word("build"), Word("run"), Word("vet"), Word("generate"), Word("install"))
CMAKE_VALUED = (Word("-S"), Word("-B"), Word("-C"), Word("-D"), Word("-U"), Word("-G"), Word("-T"), Word("-A"),
                Word("--toolchain"), Word("--install-prefix"), Word("--project-file"), Word("--preset"),
                Word("--install"), Word("--open"), Word("-P"), Word("--config"))
CTEST_VALUED = (
    Word("--preset"), Word("-C"), Word("--build-config"), Word("--test-output-size-passed"),
    Word("--test-output-size-failed"), Word("--test-output-truncation"), Word("-O"), Word("--output-log"),
    Word("--output-junit"), Word("-E"), Word("--exclude-regex"), Word("-LE"), Word("--label-exclude"), Word("-FA"),
    Word("--fixture-exclude-any"), Word("-FS"), Word("--fixture-exclude-setup"), Word("-FC"),
    Word("--fixture-exclude-cleanup"), Word("-D"), Word("--dashboard"), Word("-M"), Word("--test-model"), Word("-T"),
    Word("--test-action"), Word("--group"), Word("-S"), Word("--script"), Word("-SP"), Word("--script-new-process"),
    Word("-A"), Word("--add-notes"), Word("--tests-from-file"), Word("--exclude-from-file"), Word("--repeat"),
    Word("--repeat-until-fail"), Word("--max-width"), Word("--resource-spec-file"), Word("--test-dir"),
    Word("--extra-submit"), Word("--http-header"), Word("--timeout"), Word("--stop-time"))
CTEST_LIGHT = (Word("-N"), Word("--show-only"), Word("--print-labels"))
MAKE_LIGHT = (Word("-n"), Word("--just-print"), Word("--dry-run"), Word("--recon"), Word("-q"), Word("--question"),
              Word("-v"))
CABAL = Word("cabal")
CABAL_VALUED = (
    Word("--config-file"), Word("--http-transport"), Word("--store-dir"), Word("--active-repositories"),
    Word("--builddir"), Word("-w"), Word("--with-compiler"), Word("--with-hc-pkg"), Word("--prefix"), Word("--bindir"),
    Word("--libdir"), Word("--libsubdir"), Word("--dynlibdir"), Word("--libexecdir"), Word("--libexecsubdir"),
    Word("--datadir"), Word("--datasubdir"), Word("--docdir"), Word("--htmldir"), Word("--haddockdir"),
    Word("--sysconfdir"), Word("--program-prefix"), Word("--program-suffix"), Word("--profiling-detail"),
    Word("--library-profiling-detail"), Word("--configure-option"), Word("--package-db"), Word("-f"), Word("--flags"),
    Word("--extra-include-dirs"), Word("--ipid"), Word("--cid"), Word("--extra-lib-dirs"),
    Word("--extra-lib-dirs-static"), Word("--extra-framework-dirs"), Word("--extra-prog-path"),
    Word("--instantiate-with"), Word("--coverage-for"), Word("--cabal-lib-version"), Word("-c"), Word("--constraint"),
    Word("--preference"), Word("--solver"), Word("--write-ghc-environment-files"), Word("--doc-index-file"),
    Word("--max-backjumps"), Word("--reject-unconstrained-dependencies"), Word("--index-state"), Word("--root-cmd"),
    Word("--build-summary"), Word("--build-log"), Word("--remote-build-reporting"), Word("--haddock-html-location"),
    Word("--haddock-css"), Word("--haddock-hscolour-css"), Word("--haddock-contents-location"),
    Word("--haddock-base-url"), Word("--haddock-resources-dir"), Word("--haddock-output-dir"), Word("--test-log"),
    Word("--test-machine-log"), Word("--test-show-details"), Word("--test-wrapper"), Word("--test-options"),
    Word("--test-option"), Word("--benchmark-options"), Word("--benchmark-option"), Word("--project-dir"),
    Word("--project-file"), Word("--package-env"), Word("--overwrite-policy"), Word("--install-method"),
    Word("--installdir"))
CABAL_ROW = BARE._replace(valued_options=CABAL_VALUED)
CABAL_COMMANDS = (Word("build"), Word("v2-build"), Word("new-build"), Word("test"), Word("v2-test"), Word("bench"),
                  Word("install"), Word("v2-install"), Word("run"), Word("v2-run"), Word("new-run"))
AGDA_VALUED = (
    Word("--emacs-mode"), Word("--compile-dir"), Word("-i"), Word("--include-path"), Word("-l"), Word("--library"),
    Word("--library-file"), Word("-v"), Word("--verbose"), Word("--profile"), Word("--termination-depth"),
    Word("--instance-search-depth"), Word("--inversion-max-depth"), Word("-W"), Word("--warning"), Word("--ghc-flag"),
    Word("--with-compiler"), Word("--dependency-graph"), Word("--dependency-graph-include"), Word("--html-dir"),
    Word("--css"), Word("--latex-dir"))
GREMLINS_VALUED = (
    Word("--config"), Word("-S"), Word("--output-statuses"), Word("-t"), Word("--tags"), Word("--coverpkg"), Word("-D"),
    Word("--diff"), Word("-o"), Word("--output"), Word("-E"), Word("--exclude-files"), Word("--threshold-efficacy"),
    Word("--threshold-mcover"), Word("--workers"), Word("--test-cpu"), Word("--timeout-coefficient"))
MULL_VALUED = (
    Word("--test-program"), Word("--workers"), Word("--timeout"), Word("--ld-search-path"), Word("--coverage-info"),
    Word("--minimum-timeout"), Word("--reporters"), Word("--report-dir"), Word("--report-name"),
    Word("--report-patch-base"), Word("--mutation-score-threshold"), Word("--sqlite-busy-timeout"))
BAZEL = Word("bazel")
BAZEL_ROW = BARE._replace(valued_options=(
    Word("--bazelrc"), Word("--connect_timeout_secs"), Word("--digest_function"), Word("--experimental_cgroup_parent"),
    Word("--extra_classpath"), Word("--failure_detail_out"), Word("--io_nice_level"),
    Word("--local_startup_timeout_secs"), Word("--macos_qos_class"), Word("--max_idle_secs"), Word("--output_base"),
    Word("--output_user_root"), Word("--server_jvm_out"), Word("--host_jvm_args"), Word("--server_javabase")))
BAZEL_COMMANDS = (Word("build"), Word("test"), Word("run"))
DOCKER_VALUED = (
    Word("--config"), Word("-c"), Word("--context"), Word("-H"), Word("--host"), Word("-l"), Word("--log-level"),
    Word("--tlscacert"), Word("--tlscert"), Word("--tlskey"))

ROWS: tuple[Heavy, ...] = (
    BARE._replace(program=ProgramName("pytest"), light_with=COLLECT, targeted_by="pytest-operands"),
    BARE._replace(program=ProgramName("py.test"), light_with=COLLECT, targeted_by="pytest-operands"),
    BARE._replace(module=ModuleName("pytest"), light_with=COLLECT, targeted_by="pytest-operands"),
    BARE._replace(program=ProgramName("mutmut"), light_with=MUTMUT_LIGHT, valued_options=MUTMUT_VALUED),
    BARE._replace(module=ModuleName("mutmut"), light_with=MUTMUT_LIGHT, valued_options=MUTMUT_VALUED),
    BARE._replace(program=ProgramName("pylint"), targeted_by="python-files"),
    BARE._replace(module=ModuleName("pylint"), targeted_by="python-files"),
    BARE._replace(program=ProgramName("basedpyright"), targeted_by="python-files", valued_options=BASEDPYRIGHT_VALUED),
    BARE._replace(module=ModuleName("basedpyright"), targeted_by="python-files", valued_options=BASEDPYRIGHT_VALUED),
    *(CARGO_ROW._replace(argv=(CARGO, sub)) for sub in CARGO_WRITERS),
    CARGO_ROW._replace(argv=(CARGO, Word("mutants")), light_with=(Word("--list"), Word("--list-files"),
                                                                  Word("--emit-schema"))),
    CARGO_ROW._replace(argv=(CARGO, Word("llvm-cov")), light_with=(Word("--no-run"),)),
    CARGO_ROW._replace(argv=(CARGO, Word("install")), light_with=(Word("--list"),)),
    GO_ROW._replace(argv=(GO, Word("test")), light_with=(Word("-list"),)),
    *(GO_ROW._replace(argv=(GO, sub)) for sub in GO_BUILDS),
    BARE._replace(program=ProgramName("cmake"), valued_options=CMAKE_VALUED),
    BARE._replace(program=ProgramName("ctest"), light_with=CTEST_LIGHT, valued_options=CTEST_VALUED,
                  verbose_flags=(Word("-V"),)),
    BARE._replace(program=ProgramName("make"), light_with=MAKE_LIGHT),
    BARE._replace(program=ProgramName("gmake"), light_with=MAKE_LIGHT),
    BARE._replace(program=ProgramName("ninja"), light_with=(Word("-n"),)),
    *(CABAL_ROW._replace(argv=(CABAL, sub)) for sub in CABAL_COMMANDS),
    BARE._replace(program=ProgramName("agda"), light_with=AGDA_LIGHT, valued_options=AGDA_VALUED),
    BARE._replace(argv=(Word("gremlins"), Word("unleash")), light_with=(Word("--dry-run"), Word("-d")),
                  valued_options=GREMLINS_VALUED),
    BARE._replace(program=ProgramName("mull-runner"), light_with=(Word("--dry-run"),), valued_options=MULL_VALUED),
    *(BAZEL_ROW._replace(argv=(BAZEL, sub)) for sub in BAZEL_COMMANDS),
    BARE._replace(argv=(Word("docker"), Word("build")), valued_options=DOCKER_VALUED),
)
