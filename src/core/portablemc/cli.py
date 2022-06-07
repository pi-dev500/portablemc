# encoding: utf-8

# Copyright (C) 2021-2022  Théo Rozier
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""
CLI module for PortableMC, it provides an entry point to start Minecraft with arguments.\n
The `__main__.py` wrapper can call the entry point from the `python -m portablemc` command.
"""

from typing import cast, Union, Any, List, Dict, Optional, Type, Tuple
from argparse import ArgumentParser, Namespace, HelpFormatter
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib import parse as url_parse
from urllib.error import URLError
from datetime import datetime
from types import ModuleType
from os import path
import webbrowser
import socket
import shutil
import time
import sys

from portablemc import *


__all__ = [
    "CliContext", "CliAddon", "get_addon", "get_addon_mod",
    "register_arguments", "register_subcommands", "register_start_arguments",
    "register_login_arguments", "register_logout_arguments", "register_search_arguments",
    "register_show_arguments", "register_addon_arguments", "new_help_formatter_class",
    "cmd", "cmd_start", "cmd_login", "cmd_logout", "cmd_search", "cmd_show_about",
    "cmd_show_auth", "cmd_show_lang", "cmd_addon_show", "cmd_addon_list",
    "new_context", "new_version_manifest", "new_version", "new_start_options",
    "new_start", "new_auth_database",
    "mixin",
    "format_number", "format_bytes", "format_locale_date",
    "ellipsis_str", "anonymise_email", "get_term_width",
    "pretty_download",
    "prompt_authenticate", "prompt_yggdrasil_authenticate", "prompt_microsoft_authenticate",
    "get_message_raw", "get_message", "print_message", "prompt", "print_table", "print_task",
    "messages"
]


EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_WRONG_USAGE = 9
EXIT_VERSION_NOT_FOUND = 10
EXIT_DOWNLOAD_ERROR = 13
EXIT_AUTH_ERROR = 14
EXIT_DEPRECATED_ARGUMENT = 16
EXIT_JSON_REQUEST_ERROR = 18
EXIT_JVM_LOADING_ERROR = 19

AUTH_DB_FILE_NAME = "portablemc_auth.json"
AUTH_DB_LEGACY_FILE_NAME = "portablemc_tokens"
MANIFEST_CACHE_FILE_NAME = "portablemc_version_manifest.json"

MS_AZURE_APP_ID = "708e91b5-99f8-4a1d-80ec-e746cbb24771"

JVM_ARGS_DEFAULT = ["-Xmx2G",
                   "-XX:+UnlockExperimentalVMOptions",
                   "-XX:+UseG1GC",
                   "-XX:G1NewSizePercent=20",
                   "-XX:G1ReservePercent=20",
                   "-XX:MaxGCPauseMillis=50",
                   "-XX:G1HeapRegionSize=32M"]


class CliContext(Context):
    """ An extended `Context` class with the argument context. """
    def __init__(self, ns: Namespace):
        super().__init__(ns.main_dir, ns.work_dir)
        self.ns = ns


class CliAddon:

    __slots__ = ("module", "id", "meta")

    def __init__(self, module: ModuleType, id_: str, meta: dict):
        self.module = module
        self.id = id_
        self.meta = meta

    def get_version(self) -> str:
        """ Extract the addon's version from the package's metadata. """
        return self.meta.get("Version", "")

    def get_description(self) -> str:
        """ Extract the addon's description from the package's metadata. """
        return self.meta.get("Summary", "")

    def get_authors(self) -> str:
        from itertools import zip_longest
        names = self.meta.get("Author", "").split(", ")
        emails = self.meta.get("Author-email", "").split(", ")
        return ", ".join(map(lambda t: f"{t[0]} <{t[1]}>", zip_longest(names, emails, "")))


class CliInstallError(BaseError):
    NOT_FOUND = "not_found"
    INVALID_DIR = "invalid_dir"
    INVALID_META = "invalid_meta"
    ALREADY_INSTALLED = "already_installed"


def main(args: Optional[List[str]] = None):

    """
    Main entry point of the CLI. This function is composed of three steps:
    - Register command-line arguments
    - Parsing arguments
    - Executing command
    """

    load_addons()

    parser = register_arguments()
    ns = parser.parse_args(args or sys.argv[1:])

    command_handlers = get_command_handlers()
    command_attr = "subcommand"
    while True:
        command = getattr(ns, command_attr)
        handler = command_handlers.get(command)
        if handler is None:
            parser.print_help()
            sys.exit(EXIT_WRONG_USAGE)
        elif callable(handler):
            cmd(handler, ns)
        elif isinstance(handler, dict):
            command_attr = f"{command}_{command_attr}"
            command_handlers = handler
            continue
        sys.exit(EXIT_OK)


# Addons

addons: Dict[str, CliAddon] = {}

def load_addons():

    global addons

    prefix = "portablemc_"

    try:
        from importlib.metadata import metadata
    except ImportError:
        metadata = lambda _0: {}

    import importlib
    import pkgutil

    for pkg in pkgutil.iter_modules():
        if pkg.name.startswith(prefix) and len(pkg.name) > len(prefix):
            addon_id = pkg.name[len(prefix):]
            try:
                addon_module = importlib.import_module(pkg.name)
                addons[addon_id] = CliAddon(addon_module, addon_id, dict(metadata(pkg.name)))
            except ImportError:
                print_message("addon.import_error", {"addon": addon_id}, trace=True, critical=True)
            except (Exception,):
                print_message("addon.unknown_error", {"addon": addon_id}, trace=True, critical=True)

    for addon_id, addon in addons.items():
        if hasattr(addon.module, "load") and callable(addon.module.load):
            try:
                addon.module.load()
            except (Exception,):
                print_message("addon.unknown_error", {"addon": addon_id}, trace=True, critical=True)


def get_addon(id_: str) -> Optional[CliAddon]:
    """ Get an addon from its identifier (without the package `portablemc-` prefix). """
    return addons.get(id_)


def get_addon_mod(id_: str) -> Optional[ModuleType]:
    addon = addons.get(id_)
    return None if addon is None else addon.module


# CLI Parser

def register_arguments() -> ArgumentParser:
    _ = get_message
    parser = ArgumentParser(allow_abbrev=False, prog="portablemc", description=_("args"))
    parser.add_argument("--main-dir", help=_("args.main_dir"))
    parser.add_argument("--work-dir", help=_("args.work_dir"))
    parser.add_argument("--timeout", help=_("args.timeout"), type=float)
    register_subcommands(parser.add_subparsers(title="subcommands", dest="subcommand"))
    return parser


def register_subcommands(subparsers):
    _ = get_message
    register_search_arguments(subparsers.add_parser("search", help=_("args.search")))
    register_start_arguments(subparsers.add_parser("start", help=_("args.start")))
    register_login_arguments(subparsers.add_parser("login", help=_("args.login")))
    register_logout_arguments(subparsers.add_parser("logout", help=_("args.logout")))
    register_show_arguments(subparsers.add_parser("show", help=_("args.show")))
    register_addon_arguments(subparsers.add_parser("addon", help=_("args.addon")))


def register_search_arguments(parser: ArgumentParser):
    parser.add_argument("-l", "--local", help=get_message("args.search.local"), action="store_true")
    parser.add_argument("input", nargs="?")


def register_start_arguments(parser: ArgumentParser):
    _ = get_message
    parser.formatter_class = new_help_formatter_class(32)
    parser.add_argument("--dry", help=_("args.start.dry"), action="store_true")
    parser.add_argument("--disable-mp", help=_("args.start.disable_multiplayer"), action="store_true")
    parser.add_argument("--disable-chat", help=_("args.start.disable_chat"), action="store_true")
    parser.add_argument("--demo", help=_("args.start.demo"), action="store_true")
    parser.add_argument("--resol", help=_("args.start.resol"), type=decode_resolution)
    parser.add_argument("--jvm", help=_("args.start.jvm"))
    parser.add_argument("--jvm-args", help=_("args.start.jvm_args"))
    parser.add_argument("--no-better-logging", help=_("args.start.no_better_logging"), action="store_true")
    parser.add_argument("--anonymise", help=_("args.start.anonymise"), action="store_true")
    parser.add_argument("--no-old-fix", help=_("args.start.no_old_fix"), action="store_true")
    parser.add_argument("--lwjgl", help=_("args.start.lwjgl"), choices=["3.2.3", "3.3.0"])
    parser.add_argument("-t", "--temp-login", help=_("args.start.temp_login"), action="store_true")
    parser.add_argument("-l", "--login", help=_("args.start.login"))
    parser.add_argument("-m", "--microsoft", help=_("args.start.microsoft"), action="store_true")
    parser.add_argument("-u", "--username", help=_("args.start.username"), metavar="NAME")
    parser.add_argument("-i", "--uuid", help=_("args.start.uuid"))
    parser.add_argument("-s", "--server", help=_("args.start.server"))
    parser.add_argument("-p", "--server-port", type=int, help=_("args.start.server_port"), metavar="PORT")
    parser.add_argument("version", nargs="?", default="release")


def register_login_arguments(parser: ArgumentParser):
    parser.add_argument("-m", "--microsoft", help=get_message("args.login.microsoft"), action="store_true")
    parser.add_argument("email_or_username")


def register_logout_arguments(parser: ArgumentParser):
    parser.add_argument("-m", "--microsoft", help=get_message("args.logout.microsoft"), action="store_true")
    parser.add_argument("email_or_username")


def register_show_arguments(parser: ArgumentParser):
    _ = get_message
    subparsers = parser.add_subparsers(title="subcommands", dest="show_subcommand")
    subparsers.required = True
    subparsers.add_parser("about", help=_("args.show.about"))
    subparsers.add_parser("auth", help=_("args.show.auth"))
    subparsers.add_parser("lang", help=_("args.show.lang"))


def register_addon_arguments(parser: ArgumentParser):
    _ = get_message
    subparsers = parser.add_subparsers(title="subcommands", dest="addon_subcommand")
    subparsers.required = True
    subparsers.add_parser("list", help=_("args.addon.list"))
    show_parser = subparsers.add_parser("show", help=_("args.addon.show"))
    show_parser.add_argument("addon_id")


def new_help_formatter_class(max_help_position: int) -> Type[HelpFormatter]:

    class CustomHelpFormatter(HelpFormatter):
        def __init__(self, prog):
            super().__init__(prog, max_help_position=max_help_position)

    return CustomHelpFormatter


def decode_resolution(raw: str):
    return tuple(int(size) for size in raw.split("x"))


# Commands handlers

def get_command_handlers():
    return {
        "search": cmd_search,
        "start": cmd_start,
        "login": cmd_login,
        "logout": cmd_logout,
        "show": {
            "about": cmd_show_about,
            "auth": cmd_show_auth,
            "lang": cmd_show_lang,
        },
        "addon": {
            "list": cmd_addon_list,
            "show": cmd_addon_show
        }
    }


def cmd(handler, ns: Namespace):
    try:
        handler(ns, new_context(ns))
    except JsonRequestError as err:
        print_task("FAILED", f"json_request.error.{err.code}", {
            "url": err.url,
            "method": err.method,
            "status": err.status,
            "data": err.data,
        }, done=True, keep_previous=True)
        sys.exit(EXIT_JSON_REQUEST_ERROR)
    except (URLError, socket.gaierror, socket.timeout) as err:
        print_task("FAILED", "error.socket", {"reason": str(err)}, done=True, keep_previous=True)
        sys.exit(EXIT_FAILURE)
    except KeyboardInterrupt:
        print_task(None, "error.keyboard_interrupt", done=True, keep_previous=True)
        sys.exit(EXIT_FAILURE)


def cmd_search(ns: Namespace, ctx: CliContext):

    _ = get_message
    table = []
    search = ns.input
    no_version = (search is None)

    if ns.local:
        for version_id, mtime in ctx.list_versions():
            if no_version or search in version_id:
                table.append((version_id, format_locale_date(mtime)))
    else:
        manifest = new_version_manifest(ctx)
        search, alias = manifest.filter_latest(search)
        try:
            for version_data in manifest.all_versions():
                version_id = version_data["id"]
                if no_version or (alias and search == version_id) or (not alias and search in version_id):
                    table.append((
                        version_data["type"],
                        version_id,
                        format_locale_date(version_data["releaseTime"]),
                        _("search.flags.local") if ctx.has_version_metadata(version_id) else ""
                    ))
        except VersionManifestError as err:
            print_task("FAILED", f"version_manifest.error.{err.code}", done=True)
            sys.exit(EXIT_VERSION_NOT_FOUND)

    if len(table):
        table.insert(0, (
            _("search.name"),
            _("search.last_modified")
        ) if ns.local else (
            _("search.type"),
            _("search.name"),
            _("search.release_date"),
            _("search.flags")
        ))
        print_table(table, header=0)
        sys.exit(EXIT_OK)
    else:
        print_message("search.not_found")
        sys.exit(EXIT_VERSION_NOT_FOUND)


def cmd_start(ns: Namespace, ctx: CliContext):

    try:

        version = new_version(ctx, ns.version)

        print_task("", "start.version.resolving", {"version": version.id})
        version.prepare_meta()
        print_task("OK", "start.version.resolved", {"version": version.id}, done=True)

        version_fixes = []
        if ns.lwjgl is not None:
            fix_lwjgl_version(version, ns.lwjgl)
            version_fixes.append(f"lwjgl-{ns.lwjgl}")
            print_task("OK", "start.version.fixed.lwjgl", {"version": ns.lwjgl}, done=True)

        if len(version_fixes):
            dump_meta_name = f"{version.id}.{'.'.join(version_fixes)}.dump.json"
            with open(path.join(version.version_dir, dump_meta_name), "wt") as dump_meta_fp:
                import json
                json.dump(version.version_meta, dump_meta_fp, indent=2)

        print_task("", "start.version.jar.loading")
        version.prepare_jar()
        print_task("OK", "start.version.jar.loaded", done=True)

        print_task("", "start.assets.checking")
        version.prepare_assets()
        print_task("OK", "start.assets.checked", {"count": version.assets_count}, done=True)

        print_task("", "start.logger.loading")
        start_dl_count = version.dl.count
        version.prepare_logger()
        end_dl_count = version.dl.count

        if ns.no_better_logging or version.logging_file is None:
            print_task("OK", "start.logger.loaded", done=True)
        else:
            old_logging_file = version.logging_file
            better_logging_file = path.join(path.dirname(old_logging_file), f"portablemc-{path.basename(old_logging_file)}")
            version.logging_file = better_logging_file
            if end_dl_count != start_dl_count or not path.isfile(better_logging_file):
                # Download entries count has changed while calling prepare_logger(),
                # we must add a callback to update the pretty logging configuration.
                def _pretty_logger_finalize():
                    with open(old_logging_file, "rt") as old_logging_fh:
                        with open(better_logging_file, "wt") as better_logging_fh:
                            src = old_logging_fh.read()
                            layout_start = src.find("<PatternLayout")
                            layout_end = src.find("\n", layout_start)
                            repl = src[layout_start:layout_end]
                            src = src.replace("<XMLLayout />", repl).replace("<LegacyXMLLayout />", repl)
                            better_logging_fh.write(src)
                version.dl.add_callback(_pretty_logger_finalize)
            print_task("OK", "start.logger.loaded_pretty", done=True)

        print_task("", "start.libraries.loading")
        version.prepare_libraries()
        libs_count = len(version.classpath_libs) + len(version.native_libs)
        print_task("OK", "start.libraries.loaded", {"count": libs_count}, done=True)

        if ns.jvm is None:
            print_task("", "start.jvm.loading")
            version.prepare_jvm()
            print_task("OK", "start.jvm.loaded", {"version": version.jvm_version}, done=True)

        if version.dl.count and len(pretty_download(version.dl).fails):
            sys.exit(EXIT_DOWNLOAD_ERROR)

        if ns.dry:
            return

        # If download is successful, reset the DownloadList to garbage collect all entries
        # and reduce memory footprint of the CLI while Minecraft is running.
        version.dl.reset()

        start_opts = new_start_options(ctx)
        start_opts.disable_multiplayer = ns.disable_mp
        start_opts.disable_chat = ns.disable_chat
        start_opts.demo = ns.demo
        start_opts.server_address = ns.server
        start_opts.server_port = ns.server_port
        start_opts.jvm_exec = ns.jvm
        start_opts.old_fix = not ns.no_old_fix

        if ns.resol is not None and len(ns.resol) == 2:
            start_opts.resolution = ns.resol

        if ns.login is not None:
            start_opts.auth_session = prompt_authenticate(ctx, ns.login, not ns.temp_login, ns.microsoft, ns.anonymise)
            if start_opts.auth_session is None:
                sys.exit(EXIT_AUTH_ERROR)
        else:
            if ns.microsoft:
                print_task("WARN", "auth.microsoft_requires_email", done=True)
            start_opts.uuid = ns.uuid
            start_opts.username = ns.username

        print_task("", "start.starting")

        start = new_start(ctx, version)
        start.prepare(start_opts)
        start.jvm_args.extend(JVM_ARGS_DEFAULT if ns.jvm_args is None else ns.jvm_args.split())

        print_task("OK", "start.starting_info", {
            "username": start.args_replacements.get("auth_player_name", "n/a"),
            "uuid": start.args_replacements.get("auth_uuid", "n/a")
        }, done=True)

        start.start()

        sys.exit(EXIT_OK)

    except VersionManifestError as err:
        print_task("FAILED", f"version_manifest.error.{err.code}", done=True)
        sys.exit(EXIT_VERSION_NOT_FOUND)
    except VersionError as err:
        print_task("FAILED", f"start.version.error.{err.code}", {"version": err.version}, done=True)
        sys.exit(EXIT_VERSION_NOT_FOUND)
    except JvmLoadingError as err:
        print_task("FAILED", f"start.jvm.error.{err.code}", done=True)
        sys.exit(EXIT_JVM_LOADING_ERROR)


def cmd_login(ns: Namespace, ctx: CliContext):
    sess = prompt_authenticate(ctx, ns.email_or_username, True, ns.microsoft)
    sys.exit(EXIT_AUTH_ERROR if sess is None else EXIT_OK)


def cmd_logout(ns: Namespace, ctx: CliContext):
    task_args = {"email": ns.email_or_username}
    print_task("", "logout.microsoft.pending" if ns.microsoft else "logout.yggdrasil.pending", task_args)
    auth_db = new_auth_database(ctx)
    auth_db.load()
    session = auth_db.remove(ns.email_or_username, MicrosoftAuthSession if ns.microsoft else YggdrasilAuthSession)
    if session is not None:
        session.invalidate()
        auth_db.save()
        print_task("OK", "logout.success", task_args, done=True)
        sys.exit(EXIT_OK)
    else:
        print_task("FAILED", "logout.unknown_session", task_args, done=True)
        sys.exit(EXIT_AUTH_ERROR)


def cmd_show_about(_ns: Namespace, _ctx: CliContext):
    print(f"Version: {LAUNCHER_VERSION}")
    print(f"Authors: {', '.join(LAUNCHER_AUTHORS)}")
    print(f"Website: {LAUNCHER_URL}")
    print(f"License: {LAUNCHER_COPYRIGHT}")
    print( "         This program comes with ABSOLUTELY NO WARRANTY. This is free software,")
    print( "         and you are welcome to redistribute it under certain conditions.")
    print( "         See <https://www.gnu.org/licenses/gpl-3.0.html>.")


def cmd_show_auth(_ns: Namespace, ctx: CliContext):
    auth_db = new_auth_database(ctx)
    auth_db.load()
    lines = [("Type", "Email", "Username", "UUID")]  # Intentionally not i18n for now
    for auth_type, auth_type_sessions in auth_db.sessions.items():
        for email, sess in auth_type_sessions.items():
            lines.append((auth_type, email, sess.username, sess.uuid))
    print_table(lines, header=0)


def cmd_show_lang(_ns: Namespace, _ctx: CliContext):
    lines = []
    for key, msg in messages.items():
        lines.append((key, msg))
    lines.sort(key=lambda tup: tup[0])
    lines.insert(0, ("Key", "Message"))  # Intentionally not i18n for now
    print_table(lines, header=0)


def cmd_addon_list(_ns: Namespace, _ctx: CliContext):

    _ = get_message

    lines = [(
        _("addon.list.id", count=len(addons)),
        _("addon.list.version"),
        _("addon.list.authors"),
    )]

    for addon_id, addon in addons.items():
        lines.append((
            addon_id,
            addon.get_version(),
            addon.get_authors()
        ))

    print_table(lines, header=0)


def cmd_addon_show(ns: Namespace, _ctx: CliContext):

    addon_id = ns.addon_id
    addon = addons.get(addon_id)

    if addon is None:
        print_message("addon.show.not_found", {"addon": addon_id})
        sys.exit(EXIT_FAILURE)
    else:
        _ = get_message
        print_message("addon.show.version", {"version": addon.get_version()})
        print_message("addon.show.authors", {"authors": addon.get_authors()})
        print_message("addon.show.description", {"description": addon.get_description()})
        sys.exit(EXIT_OK)


# Constructors to override

def new_context(ns: Namespace) -> CliContext:
    """
    Returns a new game context, must extend `CliContext`.
    This function is made for mixin, you can change it from addons.
    """
    return CliContext(ns)


def new_version_manifest(ctx: CliContext) -> VersionManifest:
    """
    Returns a new version manifest instance for the given context.
    This function is made for mixin, you can change it from addons.
    """
    return VersionManifest(path.join(ctx.work_dir, MANIFEST_CACHE_FILE_NAME), ctx.ns.timeout)


def new_auth_database(ctx: CliContext) -> AuthDatabase:
    """
    Returns a new authentication database instance for the given context.
    This function is made for mixin, you can change it from addons.
    """
    return AuthDatabase(path.join(ctx.work_dir, AUTH_DB_FILE_NAME), path.join(ctx.work_dir, AUTH_DB_LEGACY_FILE_NAME))


def new_version(ctx: CliContext, version_id: str) -> Version:
    """
    Returns a new version instance for the given context and version identifier.
    This function is made for mixin, you can change it from addons. For example
    to support additional "version protocols" such a `fabric:` or `forge:` from
    official add-ons.
    """
    manifest = new_version_manifest(ctx)
    version_id, _alias = manifest.filter_latest(version_id)
    version = Version(ctx, version_id)
    version.manifest = manifest
    return version


def new_start(_ctx: CliContext, version: Version) -> Start:
    """
    Returns a new start instance for the given context and version
    (the context should be the same as version's context).
    This function is made for mixin, you can change it from addons.
    """
    return Start(version)


def new_start_options(_ctx: CliContext) -> StartOptions:
    """
    Returns new start options for the given context.
    This function is made for mixin, you can change it from addons.
    """
    return StartOptions()


# Dynamic fixing method

def fix_lwjgl_version(version: Version, lwjgl_version: str):

    lwjgl_libs = [
        "lwjgl",
        "lwjgl-jemalloc",
        "lwjgl-openal",
        "lwjgl-opengl",
        "lwjgl-glfw",
        "lwjgl-stb",
        "lwjgl-tinyfd",
    ]

    if lwjgl_version == "3.2.3":
        lwjgl_natives = {
            "arm32": {"linux": "natives-linux-arm32"},
            "arm64": {"linux": "natives-linux-arm64"},
            "x86": {"windows": "natives-windows-x86"},
            "x86_64": {"windows": "natives-windows", "linux": "natives-linux", "osx": "natives-macos"}
        }
    elif lwjgl_version == "3.3.0":
        lwjgl_natives = {
            "arm32": {"linux": "natives-linux-arm32"},
            "arm64": {"windows": "natives-windows-arm64", "linux": "natives-linux-arm64", "osx": "natives-macos-arm64"},
            "x86": {"windows": "natives-windows-x86"},
            "x86_64": {"windows": "natives-windows", "linux": "natives-linux", "osx": "natives-macos"}
        }
    else:
        raise ValueError(f"Unsupported LWJGL version {lwjgl_version}")

    meta_libraries: list = version.version_meta["libraries"]

    libraries_to_remove = []
    for idx, lib_obj in enumerate(meta_libraries):
        if "name" in lib_obj and lib_obj["name"].startswith("org.lwjgl:"):
            libraries_to_remove.append(idx)

    for idx_to_remove in reversed(libraries_to_remove):
        meta_libraries.pop(idx_to_remove)

    maven_repo_url = "https://repo1.maven.org/maven2"

    for lwjgl_lib in lwjgl_libs:

        lib_path = f"org/lwjgl/{lwjgl_lib}/{lwjgl_version}/{lwjgl_lib}-{lwjgl_version}.jar"
        lib_url = f"{maven_repo_url}/{lib_path}"
        lib_name = f"org.lwjgl:{lwjgl_lib}:{lwjgl_version}"

        meta_libraries.append({
            "downloads": {
                "artifact": {
                    "path": lib_path,
                    "url": lib_url
                }
            },
            "name": lib_name
        })

        for lwjgl_arch, lwjgl_arch_natives in lwjgl_natives.items():

            arch_classifiers = {}

            for lwjgl_os, lwjgl_classifier in lwjgl_arch_natives.items():
                classifier_path = f"org/lwjgl/{lwjgl_lib}/{lwjgl_version}/{lwjgl_lib}-{lwjgl_version}-{lwjgl_classifier}.jar"
                classifier_url = f"{maven_repo_url}/{classifier_path}"
                arch_classifiers[lwjgl_classifier] = {
                    "path": classifier_path,
                    "url": classifier_url
                }

            meta_libraries.append({
                "downloads": {
                    "artifact": {
                        "path": lib_path,
                        "url": lib_url
                    },
                    "classifiers": arch_classifiers
                },
                "natives": lwjgl_arch_natives,
                "name": lib_name,
                "rules": [{"action": "allow", "os": {"arch": lwjgl_arch}}]
            })


# CLI utilities

def mixin(name: Optional[str] = None, into: Optional[object] = None):
    def mixin_decorator(func):
        orig_obj = into or sys.modules[__name__]
        orig_name = name or func.__name__
        orig_func = getattr(orig_obj, orig_name, None)
        if orig_func is None:
            raise ValueError(f"The function '{orig_obj}.{orig_name}' you are trying to mixin does not exists.")
        def wrapper(*w_args, **w_kwargs):
            return func(orig_func, *w_args, **w_kwargs)
        setattr(orig_obj, orig_name, wrapper)
        return func
    return mixin_decorator


def format_locale_date(raw: Union[str, float]) -> str:
    if isinstance(raw, float):
        return datetime.fromtimestamp(raw).strftime("%c")
    else:
        return from_iso_date(str(raw)).strftime("%c")


def format_number(n: int) -> str:
    """ Return a number with suffix k, M, G or nothing. The string is always 6 chars unless the size exceed 1 TB. """
    if n < 1000:
        return "{:d}".format(int(n))
    elif n < 1000000:
        return "{:.1f}k".format(int(n / 100) / 10)
    elif n < 1000000000:
        return "{:.1f}M".format(int(n / 100000) / 10)
    else:
        return "{:.1f}G".format(int(n / 100000000) / 10)


def format_bytes(n: int) -> str:
    """ Return a byte with suffix B, kB, MB and GB. The string is always 7 chars unless the size exceed 1 TB. """
    return f"{format_number(n)}B"


def ellipsis_str(string: str, length: int) -> str:
    return f"{string[:(length - 3)]}..." if len(string) > length else string


def anonymise_email(email: str) -> str:
    def anonymise_part(email_part: str) -> str:
        return f"{email_part[0]}{'*' * (len(email_part) - 2)}{email_part[-1]}"
    parts = []
    for i, part in enumerate(email.split("@", maxsplit=1)):
        if i == 0:
            parts.append(anonymise_part(part))
        else:
            parts.append(".".join((anonymise_part(server_part) if j == 0 else server_part for j, server_part in enumerate(part.split(".", maxsplit=1)))))
    return "@".join(parts)


_term_width = 0
_term_width_update_time = 0
def get_term_width() -> int:
    global _term_width, _term_width_update_time
    now = time.monotonic()
    if now - _term_width_update_time > 1:
        _term_width_update_time = now
        _term_width = shutil.get_terminal_size().columns
    return _term_width


# Pretty download

def pretty_download(dl_list: DownloadList) -> DownloadReport:

    """
    Download a `DownloadList` with a pretty progress bar using the `print_task` function.
    Returns True if no error happened.
    """

    start_time = time.perf_counter()
    last_print_time: Optional[bool] = None
    called_once = False

    dl_text = get_message("download.downloading")
    non_path_len = len(dl_text) + 21

    def progress_callback(progress: DownloadProgress):
        nonlocal called_once, last_print_time
        now = time.perf_counter()
        if last_print_time is None or (now - last_print_time) > 0.1:
            last_print_time = now
            speed = format_bytes(int(progress.size / (now - start_time)))
            percentage = 100.0 if progress.total == 0 else min(100.0, progress.size / progress.total * 100.0)
            entries = ", ".join((entry.name for entry in progress.entries))
            path_len = max(0, min(80, get_term_width()) - non_path_len - len(speed))
            print(f"\r[      ] {dl_text} {entries[:path_len].ljust(path_len)} {percentage:6.2f}% {speed}/s", end="")
            called_once = True

    def complete_task(errors_count: int = 0):

        errors_text = get_message("download.no_error") if errors_count == 0 else get_message("download.errors", count=errors_count)
        result_text = get_message("download.downloaded",
                                  success_count=dl_list.count - errors_count,
                                  total_count=dl_list.count,
                                  size=format_bytes(dl_list.size).lstrip(" "),
                                  duration=(time.perf_counter() - start_time),
                                  errors=errors_text)

        result_len = max(0, min(80, get_term_width()) - 9)
        template = "[  OK  ] {}"
        if called_once:
            template = f"\r{template}"
        print(template.format(result_text[:result_len].ljust(result_len)))

    try:
        dl_report = dl_list.download_files(progress_callback=progress_callback)
        complete_task(len(dl_report.fails))
        if len(dl_report.fails):
            for entry, entry_error in dl_report.fails.items():
                entry_error_msg = get_message(f"download.error.{entry_error}")
                print(f"         {entry.url}: {entry_error_msg}")
        return dl_report
    except KeyboardInterrupt:
        if called_once:
            print()
        raise


# Authentication

def prompt_authenticate(ctx: CliContext, email: str, cache_in_db: bool, microsoft: bool, anonymise: bool = False) -> Optional[AuthSession]:

    """
    Prompt the user to login using the given email (or legacy username) for specific service (Microsoft or
    Yggdrasil) and return the :class:`AuthSession` if successful, None otherwise. This function handles task
    printing and all exceptions are caught internally.
    """

    auth_db = new_auth_database(ctx)
    auth_db.load()

    task_text = "auth.microsoft" if microsoft else "auth.yggdrasil"
    task_text_args = {"email": anonymise_email(email) if anonymise else email}
    print_task("", task_text, task_text_args)

    session = auth_db.get(email, MicrosoftAuthSession if microsoft else YggdrasilAuthSession)
    if session is not None:
        try:
            if not session.validate():
                print_task("", "auth.refreshing")
                session.refresh()
                auth_db.save()
                print_task("OK", "auth.refreshed", task_text_args, done=True)
            else:
                print_task("OK", "auth.validated", task_text_args, done=True)
            return session
        except AuthError as err:
            print_task("FAILED", f"auth.error.{err.code}", {"details": err.details}, done=True, keep_previous=True)

    print_task("..", task_text, task_text_args, done=True)

    try:
        if microsoft:
            session = prompt_microsoft_authenticate(auth_db.get_client_id(), email)
        else:
            session = prompt_yggdrasil_authenticate(auth_db.get_client_id(), email)
        if session is None:
            return None
        if cache_in_db:
            print_task("", "auth.caching")
            auth_db.put(email, session)
            auth_db.save()
        print_task("OK", "auth.logged_in", done=True)
        return session
    except AuthError as err:
        print_task("FAILED", f"auth.error.{err.code}", {"details": err.details}, done=True, keep_previous=True)
        return None


def prompt_yggdrasil_authenticate(client_id: str, email_or_username: str) -> Optional[YggdrasilAuthSession]:
    print_task(None, "auth.yggdrasil.enter_password")
    password = prompt(password=True)
    if password is None:
        print_task("FAILED", "cancelled")
        return None
    else:
        return YggdrasilAuthSession.authenticate(client_id, email_or_username, password)


def prompt_microsoft_authenticate(client_id: str, email: str) -> Optional[MicrosoftAuthSession]:

    server_port = 12782
    app_id = MS_AZURE_APP_ID
    redirect_auth = "http://localhost:{}".format(server_port)
    code_redirect_uri = "{}/code".format(redirect_auth)
    exit_redirect_uri = "{}/exit".format(redirect_auth)

    import uuid
    nonce = uuid.uuid4().hex

    if not webbrowser.open(MicrosoftAuthSession.get_authentication_url(app_id, code_redirect_uri, email, nonce)):
        print_task("FAILED", "auth.microsoft.no_browser", done=True)
        return None

    class AuthServer(HTTPServer):

        def __init__(self):
            super().__init__(("", server_port), RequestHandler)
            self.timeout = 0.5
            self.ms_auth_done = False
            self.ms_auth_id_token: Optional[str] = None
            self.ms_auth_code: Optional[str] = None

    class RequestHandler(BaseHTTPRequestHandler):

        server_version = "PortableMC/{}".format(LAUNCHER_VERSION)

        def __init__(self, request: bytes, client_address: Tuple[str, int], auth_server: AuthServer) -> None:
            super().__init__(request, client_address, auth_server)

        def log_message(self, _format: str, *args: Any):
            return

        def send_auth_response(self, msg: str):
            self.end_headers()
            self.wfile.write("{}{}".format(msg, "\n\nClose this tab and return to the launcher." if cast(AuthServer, self.server).ms_auth_done else "").encode())
            self.wfile.flush()

        def do_POST(self):
            if self.path.startswith("/code") and self.headers.get_content_type() == "application/x-www-form-urlencoded":
                content_length = int(self.headers.get("Content-Length"))
                qs = url_parse.parse_qs(self.rfile.read(content_length).decode())
                auth_server = cast(AuthServer, self.server)
                if "code" in qs and "id_token" in qs:
                    self.send_response(307)
                    # We log out the user directly after authorization, this just clear the browser cache to allow
                    # another user to authenticate with another email after. This doesn't invalid the access token.
                    self.send_header("Location", MicrosoftAuthSession.get_logout_url(app_id, exit_redirect_uri))
                    auth_server.ms_auth_id_token = qs["id_token"][0]
                    auth_server.ms_auth_code = qs["code"][0]
                    self.send_auth_response("Redirecting...")
                elif "error" in qs:
                    self.send_response(400)
                    auth_server.ms_auth_done = True
                    self.send_auth_response("Error: {} ({}).".format(qs["error_description"][0], qs["error"][0]))
                else:
                    self.send_response(404)
                    self.send_auth_response("Missing parameters.")
            else:
                self.send_response(404)
                self.send_auth_response("Unexpected page.")

        def do_GET(self):
            auth_server = cast(AuthServer, self.server)
            if self.path.startswith("/exit"):
                self.send_response(200)
                auth_server.ms_auth_done = True
                self.send_auth_response("Logged in.")
            else:
                self.send_response(404)
                self.send_auth_response("Unexpected page.")

    print_task("", "auth.microsoft.opening_browser_and_listening")

    try:
        with AuthServer() as server:
            while not server.ms_auth_done:
                server.handle_request()
    except KeyboardInterrupt:
        pass

    if server.ms_auth_code is None:
        print_task("FAILED", "auth.microsoft.failed_to_authenticate", done=True)
        return None
    else:
        print_task("", "auth.microsoft.processing")
        if MicrosoftAuthSession.check_token_id(server.ms_auth_id_token, email, nonce):
            return MicrosoftAuthSession.authenticate(client_id, app_id, server.ms_auth_code, code_redirect_uri)
        else:
            print_task("FAILED", "auth.microsoft.incoherent_dat", done=True)
            return None

# Messages

def get_message_raw(key: str, kwargs: Optional[dict]) -> str:
    try:
        return messages[key].format_map(kwargs or {})
    except KeyError:
        return key

def get_message(key: str, **kwargs) -> str:
    return get_message_raw(key, kwargs)


def print_message(key: str, kwargs: Optional[dict] = None, *, end: str = "\n", trace: bool = False, critical: bool = False):
    if critical:
        print("\033[31m", end="")
    print(get_message_raw(key, kwargs), end=end)
    if trace:
        import traceback
        traceback.print_exc()
    if critical:
        print("\033[0m", end="")


def prompt(password: bool = False) -> Optional[str]:
    try:
        if password:
            import getpass
            return getpass.getpass("")
        else:
            return input("")
    except KeyboardInterrupt:
        return None


def print_table(lines: List[Tuple[str, ...]], *, header: int = -1):

    if not len(lines):
        return

    columns_count = len(lines[0])
    columns_length = [0] * columns_count

    for line in lines:
        if len(line) != columns_count:
            raise ValueError(f"Inconsistent cell count '{line}', expected {columns_count}.")
        for i, cell in enumerate(line):
            cell_len = len(cell)
            if columns_length[i] < cell_len:
                columns_length[i] = cell_len

    total_length = 1 + sum(x + 3 for x in columns_length)
    max_length = get_term_width() - 1
    if total_length > max_length:
        overflow_length = total_length - max_length
        total_cell_length = sum(columns_length)
        for i in range(columns_count):
            cell_overflow_length = int(columns_length[i] / total_cell_length * overflow_length)
            overflow_length -= cell_overflow_length
            columns_length[i] -= cell_overflow_length
            if i == columns_count - 1:
                columns_length[i] -= overflow_length

    format_string = "│ {} │".format(" │ ".join((f"{{:{length}s}}" for length in columns_length)))
    columns_lines = ["─" * length for length in columns_length]
    print("┌─{}─┐".format("─┬─".join(columns_lines)))
    for i, line in enumerate(lines):
        print(format_string.format(*(ellipsis_str(cell, columns_length[j]) for j, cell in enumerate(line))))
        if i == header:
            print("├─{}─┤".format("─┼─".join(columns_lines)))
    print("└─{}─┘".format("─┴─".join(columns_lines)))


_print_task_last_len = 0
def print_task(status: Optional[str], msg_key: str, msg_args: Optional[dict] = None, *, done: bool = False, keep_previous: bool = False):
    global _print_task_last_len
    if keep_previous and _print_task_last_len != 0:
        print()
    len_limit = max(0, get_term_width() - 9)
    msg = get_message_raw(msg_key, msg_args)[:len_limit]
    missing_len = max(0, _print_task_last_len - len(msg))
    status_header = "\r         " if status is None else "\r[{:^6s}] ".format(status)
    _print_task_last_len = 0 if done else len(msg)
    print(status_header, msg, " " * missing_len, sep="", end="\n" if done else "", flush=True)


messages = {
    # Addons
    "addon.import_error": "The addon '{addon}' has failed to build because some packages is missing:",
    "addon.unknown_error": "The addon '{addon}' has failed to build for unknown reason:",
    # Args root
    "args": "PortableMC is an easy to use portable Minecraft launcher in only one Python "
            "script! This single-script launcher is still compatible with the official "
            "(Mojang) Minecraft Launcher stored in .minecraft and use it.",
    "args.main_dir": "Set the main directory where libraries, assets and versions. "
                     "This argument can be used or not by subcommand.",
    "args.work_dir": "Set the working directory where the game run and place for examples "
                     "saves, screenshots (and resources for legacy versions), it also store "
                     "runtime binaries and authentication. "
                     "This argument can be used or not by subcommand.",
    "args.timeout": "Set a global timeout (in decimal seconds) that can be used by various requests done by the launcher or "
                    "addons. A value of 0 is usually interpreted as an 'offline mode', this means that the launcher "
                    "will try to use a cached copy of the requests' response.",
    # Args search
    "args.search": "Search for Minecraft versions.",
    "args.search.local": "Search only for local installed Minecraft versions.",
    # Args start
    "args.start": "Start a Minecraft version, default to the latest release.",
    "args.start.dry": "Simulate game starting.",
    "args.start.disable_multiplayer": "Disable the multiplayer buttons (>= 1.16).",
    "args.start.disable_chat": "Disable the online chat (>= 1.16).",
    "args.start.demo": "Start game in demo mode.",
    "args.start.resol": "Set a custom start resolution (<width>x<height>).",
    "args.start.jvm": "Set a custom JVM 'javaw' executable path. If this argument is omitted a public build "
                      "of a JVM is downloaded from Mojang services.",
    "args.start.jvm_args": "Change the default JVM arguments.",
    "args.start.no_better_logging": "Disable the better logging configuration built by the launcher in "
                                    "order to improve the log readability in the console.",
    "args.start.anonymise": "Anonymise your email or username for authentication messages.",
    "args.start.no_old_fix": "Flag that disable fixes for old versions (legacy merge sort, betacraft proxy), "
                             "enabled by default.",
    "args.start.lwjgl": "Change the default LWJGL version used by Minecraft, currently supporting '3.2.3' and '3.3.0'. "
                        "This argument makes additional changes in order to support additional natives architectures. "
                        "It's not guaranteed to work with every version of Minecraft.",
    "args.start.temp_login": "Flag used with -l (--login) to tell launcher not to cache your session if "
                             "not already cached, disabled by default.",
    "args.start.login": "Use a email (or deprecated username) to authenticate using Mojang services (it override --username and --uuid).",
    "args.start.microsoft": "Login using Microsoft account, to use with -l (--login).",
    "args.start.username": "Set a custom user name to play.",
    "args.start.uuid": "Set a custom user UUID to play.",
    "args.start.server": "Start the game and auto-connect to this server address (since 1.6).",
    "args.start.server_port": "Set the server address port (given with -s, --server, since 1.6).",
    # Args login
    "args.login": "Login into your account and save the session.",
    "args.login.microsoft": "Login using Microsoft account.",
    # Args logout
    "args.logout": "Logout and invalidate a session.",
    "args.logout.microsoft": "Logout from a Microsoft account.",
    # Args show
    "args.show": "Show and debug various data.",
    "args.show.about": "Display authors, version and license of PortableMC.",
    "args.show.auth": "Debug the authentication database and supported services.",
    "args.show.lang": "Debug the language mappings used for messages translation.",
    # Args addon
    "args.addon": "Addons management subcommands.",
    "args.addon.list": "List addons.",
    "args.addon.show": "Show an addon details.",
    # Common
    "continue_using_main_dir": "Continue using this main directory ({})? (y/N) ",
    "cancelled": "Cancelled.",
    # Version manifest error
    f"version_manifest.error.{VersionManifestError.NOT_FOUND}": "Failed to load version manifest, timed out or not locally cached.",
    # Json Request
    f"json_request.error.{JsonRequestError.INVALID_RESPONSE_NOT_JSON}": "Invalid JSON response from {method} {url}, status: {status}, data: {data}",
    # Misc errors
    "error.socket": "This operation requires an operational network, but a socket error happened: {reason}",
    "error.keyboard_interrupt": "Interrupted.",
    # Command search
    "search.type": "Type",
    "search.name": "Identifier",
    "search.release_date": "Release date",
    "search.last_modified": "Last modified",
    "search.flags": "Flags",
    "search.flags.local": "local",
    "search.not_found": "No version match the input.",
    # Command logout
    "logout.yggdrasil.pending": "Logging out {email} from Mojang...",
    "logout.microsoft.pending": "Logging out {email} from Microsoft...",
    "logout.success": "Logged out {email}.",
    "logout.unknown_session": "No session for {email}.",
    # Command addon list
    "addon.list.id": "ID ({count})",
    "addon.list.version": "Version",
    "addon.list.authors": "Authors",
    # Command addon show
    "addon.show.not_found": "Addon '{addon}' not found.",
    "addon.show.version": "Version: {version}",
    "addon.show.authors": "Authors: {authors}",
    "addon.show.description": "Description: {description}",
    # Command start
    "start.version.resolving": "Resolving version {version}... ",
    "start.version.resolved": "Resolved version {version}.",
    "start.version.fixed.lwjgl": "Fixed LWJGL version to {version}",
    "start.version.jar.loading": "Loading version JAR... ",
    "start.version.jar.loaded": "Loaded version JAR.",
    f"start.version.error.{VersionError.NOT_FOUND}": "Version {version} not found.",
    f"start.version.error.{VersionError.TO_MUCH_PARENTS}": "The version {version} has to much parents.",
    f"start.version.error.{VersionError.JAR_NOT_FOUND}": "Version {version} JAR not found.",
    "start.assets.checking": "Checking assets... ",
    "start.assets.checked": "Checked {count} assets.",
    "start.logger.loading": "Loading logger... ",
    "start.logger.loaded": "Loaded logger.",
    "start.logger.loaded_pretty": "Loaded pretty logger.",
    "start.libraries.loading": "Loading libraries... ",
    "start.libraries.loaded": "Loaded {count} libraries.",
    "start.jvm.loading": "Loading Java... ",
    "start.jvm.loaded": "Loaded Mojang Java {version}.",
    f"start.jvm.error.{JvmLoadingError.UNSUPPORTED_ARCH}": "No JVM download was found for your platform architecture, "
                                                           "use --jvm argument to set the JVM executable of path to it.",
    f"start.jvm.error.{JvmLoadingError.UNSUPPORTED_VERSION}": "No JVM download was found, use --jvm argument to set "
                                                              "the JVM executable of path to it.",
    "start.starting": "Starting the game...",
    "start.starting_info": "Username: {username} ({uuid})",
    # Pretty download
    "download.downloading": "Downloading",
    "download.downloaded": "Downloaded {success_count}/{total_count} files, {size} in {duration:.1f}s ({errors}).",
    "download.no_error": "no error",
    "download.errors": "{count} errors",
    f"download.error.{DownloadReport.CONN_ERROR}": "Connection error",
    f"download.error.{DownloadReport.NOT_FOUND}": "Not found",
    f"download.error.{DownloadReport.INVALID_SIZE}": "Invalid size",
    f"download.error.{DownloadReport.INVALID_SHA1}": "Invalid SHA1",
    f"download.error.{DownloadReport.TOO_MANY_REDIRECTIONS}": "Too many redirections",
    # Auth common
    "auth.refreshing": "Invalid session, refreshing...",
    "auth.refreshed": "Session refreshed for {email}.",
    "auth.validated": "Session validated for {email}.",
    "auth.caching": "Caching your session...",
    "auth.logged_in": "Logged in",
    "auth.microsoft_requires_email": "Even if you are using -m (`--microsoft`), you must use `-l` argument with your "
                                     "Microsoft email.",
    # Auth Yggdrasil
    "auth.yggdrasil": "Authenticating {email} with Mojang...",
    "auth.yggdrasil.enter_password": "Password: ",
    f"auth.error.{AuthError.YGGDRASIL}": "{details}",
    # Auth Microsoft
    "auth.microsoft": "Authenticating {email} with Microsoft...",
    "auth.microsoft.no_browser": "Failed to open Microsoft login page, no web browser is supported.",
    "auth.microsoft.opening_browser_and_listening": "Opened authentication page in browser...",
    "auth.microsoft.failed_to_authenticate": "Failed to authenticate.",
    "auth.microsoft.processing": "Processing authentication against Minecraft services...",
    "auth.microsoft.incoherent_data": "Incoherent authentication data, please retry.",
    f"auth.error.{AuthError.MICROSOFT_INCONSISTENT_USER_HASH}": "Inconsistent user hash.",
    f"auth.error.{AuthError.MICROSOFT_DOES_NOT_OWN_MINECRAFT}": "This account does not own Minecraft.",
    f"auth.error.{AuthError.MICROSOFT_OUTDATED_TOKEN}": "The token is no longer valid.",
    f"auth.error.{AuthError.MICROSOFT}": "Misc error: {details}."
}