import os
import uuid
import time
import json
import zipfile
import hashlib
import threading
import urllib.error
import urllib.parse
import tkinter as tk
import urllib.request
from tkinter import ttk
from tkinter import filedialog
import xml.etree.ElementTree as ET

API_URL = "https://api.modrinth.com/v2/search"
USER_AGENT = "gingrspacecadet/minecraft-modpack-maker/0.1.0"

def open_url(url, timeout=15):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT},
    )

    last_error = None

    for attempt in range(3):
        try:
            return urllib.request.urlopen(request, timeout=timeout)
        except urllib.error.HTTPError:
            raise
        except urllib.error.URLError as error:
            last_error = error

            if attempt < 2:
                time.sleep(1 << attempt)

    raise last_error

def get_url(url):
    with open_url(url) as response:
        return json.load(response)

def get_modrinth_versions(project_id):
    params = urllib.parse.urlencode({
        "include_changelog": "false",
    })

    return get_url(
        f"https://api.modrinth.com/v2/project/{project_id}/version?{params}"
    )

def get_modrinth_version(version_id):
    return get_url(
        f"https://api.modrinth.com/v2/version/{version_id}"
    )

def get_modrinth_project(project_id):
    return get_url(
        f"https://api.modrinth.com/v2/project/{project_id}"
    )

def get_xml_url(url):
    with open_url(url) as response:
        return ET.fromstring(response.read())

def get_loader_version(minecraft_version, loader):
    encoded_version = urllib.parse.quote(minecraft_version, safe="")

    if loader == "fabric":
        versions = get_url(
            f"https://meta.fabricmc.net/v2/versions/loader/{encoded_version}"
        )

        for version in versions:
            if version["loader"]["stable"]:
                return version["loader"]["version"]

        if versions:
            return versions[0]["loader"]["version"]

        raise ValueError(
            f"No Fabric Loader found for Minecraft {minecraft_version}"
        )

    if loader == "quilt":
        versions = get_url(
            f"https://meta.quiltmc.org/v3/versions/loader/{encoded_version}"
        )

        for version in versions:
            if version["loader"]["stable"]:
                return version["loader"]["version"]

        if versions:
            return versions[0]["loader"]["version"]

        raise ValueError(
            f"No Quilt Loader found for Minecraft {minecraft_version}"
        )

    if loader == "forge":
        root = get_xml_url(
            "https://maven.minecraftforge.net/net/minecraftforge/forge/maven-metadata.xml"
        )

        versions = [
            element.text
            for element in root.findall("./versioning/versions/version")
            if element.text is not None
        ]

        prefix = f"{minecraft_version}-"
        matches = [
            version[len(prefix):]
            for version in versions
            if version.startswith(prefix)
        ]

        if not matches:
            raise ValueError(
                f"No Forge version found for Minecraft {minecraft_version}"
            )

        return matches[-1]

    if loader == "neoforge":
        if minecraft_version == "1.20.1":
            artifact = "forge"
            prefix = "1.20.1-"
        else:
            artifact = "neoforge"

            if minecraft_version.startswith("1."):
                prefix = f"{minecraft_version[2:]}."
            else:
                prefix = f"{minecraft_version}."

        root = get_xml_url(
            f"https://maven.neoforged.net/releases/net/neoforged/{artifact}/maven-metadata.xml"
        )

        versions = [
            element.text
            for element in root.findall("./versioning/versions/version")
            if element.text is not None
        ]

        matches = [
            version
            for version in versions
            if version.startswith(prefix)
        ]

        if not matches:
            raise ValueError(
                f"No NeoForge version found for Minecraft {minecraft_version}"
            )

        return matches[-1]

    raise ValueError(f"Unsupported loader: {loader}")

def get_file_environment(release):
    environments = {
        "client_and_server": {
            "client": "required",
            "server": "required",
        },
        "client_only": {
            "client": "required",
            "server": "unsupported",
        },
        "client_only_server_optional": {
            "client": "required",
            "server": "optional",
        },
        "server_only": {
            "client": "unsupported",
            "server": "required",
        },
        "server_only_client_optional": {
            "client": "optional",
            "server": "required",
        },
        "dedicated_server_only": {
            "client": "unsupported",
            "server": "required",
        },
        "singleplayer_only": {
            "client": "required",
            "server": "unsupported",
        },
        "client_or_server": {
            "client": "optional",
            "server": "optional",
        },
        "client_or_server_prefers_both": {
            "client": "optional",
            "server": "optional",
        },
    }

    return environments.get(release["environment"])

def create_mrpack(path, resolution):
    if resolution["external"]:
        raise ValueError(
            "Cannot export a modpack with unresolved external dependencies"
        )

    minecraft_version = resolution["minecraft_version"]
    loader = resolution["loader"]
    loader_ids = {
        "fabric": "fabric-loader",
        "quilt": "quilt-loader",
        "forge": "forge",
        "neoforge": "neoforge",
    }

    if loader not in loader_ids:
        raise ValueError(f"Unsupported loader: {loader}")

    loader_version = get_loader_version(
        minecraft_version,
        loader,
    )

    dependencies = {
        "minecraft": minecraft_version,
        loader_ids[loader]: loader_version,
    }

    files = []

    for project_id, release in resolution["releases"].items():
        file = get_primary_file(release)
        hashes = file["hashes"]

        if "sha1" not in hashes or "sha512" not in hashes:
            raise ValueError(
                f"Missing required hashes for {file['filename']}"
            )

        entry = {
            "path": f"mods/{file['filename']}",
            "hashes": {
                "sha1": hashes["sha1"],
                "sha512": hashes["sha512"],
            },
            "downloads": [
                file["url"],
            ],
            "fileSize": file["size"],
        }

        environment = get_file_environment(release)

        if environment is not None:
            entry["env"] = environment

        files.append(entry)

    pack = {
        "formatVersion": 1,
        "game": "minecraft",
        "versionId": str(uuid.uuid4()),
        "name": os.path.splitext(os.path.basename(path))[0],
        "files": files,
        "dependencies": dependencies,
    }

    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "modrinth.index.json",
            json.dumps(pack, indent=4) + "\n",
        )

def handle_export():
    if current_resolution is None:
        return

    path = filedialog.asksaveasfilename(
        title="Export Modpack",
        defaultextension=".mrpack",
        filetypes=[
            ("Modrinth Modpack", "*.mrpack"),
        ],
    )

    if not path:
        return

    export_button.config(state=tk.DISABLED)

    resolution = current_resolution

    def worker():
        try:
            create_mrpack(path, resolution)
        except Exception as error:
            root.after(
                0,
                lambda error=error: export_failed(error),
            )
            return

        root.after(
            0,
            lambda: export_complete(path),
        )

    threading.Thread(target=worker, daemon=True).start()

def export_complete(path):
    export_button.config(state=tk.NORMAL)
    download_status.set(
        f"Exported {os.path.basename(path)}"
    )

def export_failed(error):
    export_button.config(state=tk.NORMAL)
    download_status.set(f"Export failed: {error}")

def search_modrinth(query=""):
    facets = [
        ["project_type:mod"],
    ]

    params = urllib.parse.urlencode({
        "query": query,
        "facets": json.dumps(facets),
        "limit": 20,
    })

    return get_url(f"{API_URL}?{params}")["hits"]

def update_tree():
    global search_job

    search_job = None

    query = search_var.get()
    generation = search_generation

    def worker():
        try:
            results = search_modrinth(query)
        except Exception as error:
            root.after(0, lambda error=error: search_failed(generation, error))
            return

        root.after(0, lambda: populate_tree(generation, results))

    threading.Thread(target=worker, daemon=True).start()

def populate_tree(generation, results):
    if generation != search_generation:
        return

    for item in available_view.get_children():
        available_view.delete(item)

    for mod in results:
        project_id = mod["project_id"]
        mods[project_id] = mod

        available_view.insert("", tk.END, iid=project_id, values=(mod["title"], mod["author"], mod["downloads"]))

def search_failed(generation, error):
    if generation != search_generation:
        return

    print(f"Search failed: {error}")

def on_search_changed(*args):
    global search_job
    global search_generation

    search_generation += 1

    if search_job is not None:
        root.after_cancel(search_job)

    search_job = root.after(300, update_tree)

def handle_add(event=None):
    project_id = available_view.focus()

    if not project_id or selected_view.exists(project_id):
        return "break"

    mod = mods[project_id]

    selected_view.insert("", tk.END, iid=project_id, values=(mod["title"], mod["author"], mod["downloads"]))

    return "break"

def handle_remove(event=None):
    for project_id in selected_view.selection():
        selected_view.delete(project_id)

    return "break"

def compatible_releases(mod_versions, project_id, minecraft_version, loader):
    releases = mod_versions[project_id]

    return [
        release
        for release in releases
        if release["status"] == "listed"
        and minecraft_version in release["game_versions"]
        and loader in release["loaders"]
    ]

def release_satisfies_constraints(release, constraints):
    for dependency in constraints:
        version_id = dependency.get("version_id")

        if version_id is not None and release["id"] != version_id:
            return False

    return True

def release_is_forbidden(release, dependencies):
    for dependency in dependencies:
        version_id = dependency.get("version_id")

        if version_id is not None and release["id"] == version_id:
            return True

        project_id = dependency.get("project_id")

        if project_id is not None and release["project_id"] == project_id:
            return True

    return False

def release_sort_key(release):
    version_type = {
        "release": 2,
        "beta": 1,
        "alpha": 0,
    }.get(release["version_type"], -1)

    return (
        version_type,
        release["date_published"],
        release["id"],
    )

def dependency_project_id(dependency):
    project_id = dependency.get("project_id")

    if project_id is not None:
        return project_id

    version_id = dependency.get("version_id")

    if version_id is None:
        return None

    if version_id not in mod_versions_by_id:
        mod_versions_by_id[version_id] = get_modrinth_version(version_id)

    return mod_versions_by_id[version_id]["project_id"]

def compatible_dependency_releases(project_id, minecraft_version, loader, constraints, forbidden):
    releases = mod_versions[project_id]

    candidates = [
        release
        for release in releases
        if release["status"] == "listed"
        and minecraft_version in release["game_versions"]
        and loader in release["loaders"]
        and release_satisfies_constraints(release, constraints)
        and not release_is_forbidden(release, forbidden)
    ]

    return sorted(candidates, key=release_sort_key, reverse=True)

def assignment_is_valid(project_id, release, requirements, forbidden):
    return (
        release_satisfies_constraints(
            release,
            requirements.get(project_id, []),
        )
        and not release_is_forbidden(
            release,
            forbidden.get(project_id, []),
        )
    )

def resolve_releases(minecraft_version, loader, selected):
    requirements = {
        project_id: []
        for project_id in selected
    }

    forbidden = {}
    assignments = {}
    external = []

    def resolve(requirements, forbidden, assignments, external):
        for project_id, release in assignments.items():
            if not assignment_is_valid(
                project_id,
                release,
                requirements,
                forbidden,
            ):
                return None

        pending = [
            project_id
            for project_id in requirements
            if project_id not in assignments
        ]

        if not pending:
            return {
                "releases": assignments,
                "external": external,
            }

        candidates = {}

        for project_id in pending:
            if project_id not in mod_versions:
                mod_versions[project_id] = get_modrinth_versions(project_id)

            candidates[project_id] = compatible_dependency_releases(
                project_id,
                minecraft_version,
                loader,
                requirements[project_id],
                forbidden.get(project_id, []),
            )

            if not candidates[project_id]:
                return None

        project_id = min(
            pending,
            key=lambda project_id: len(candidates[project_id]),
        )

        for release in candidates[project_id]:
            if project_id not in mod_projects_by_id:
                mod_projects_by_id[project_id] = get_modrinth_project(project_id)

            next_requirements = {
                project: dependencies[:]
                for project, dependencies in requirements.items()
            }

            next_forbidden = {
                project: dependencies[:]
                for project, dependencies in forbidden.items()
            }

            next_assignments = dict(assignments)
            next_external = external[:]

            next_assignments[project_id] = release

            failed = False

            for dependency in release["dependencies"]:
                dependency_type = dependency["dependency_type"]

                if dependency_type in ("optional", "embedded"):
                    continue

                dependency_project = dependency_project_id(dependency)

                if dependency_type == "required":
                    if dependency_project is None:
                        next_external.append(dependency)
                        continue

                    if dependency_project not in next_requirements:
                        next_requirements[dependency_project] = []

                    next_requirements[dependency_project].append(dependency)

                elif dependency_type == "incompatible":
                    if dependency_project is None:
                        continue

                    if dependency_project not in next_forbidden:
                        next_forbidden[dependency_project] = []

                    next_forbidden[dependency_project].append(dependency)

                else:
                    failed = True
                    break

            if failed:
                continue

            result = resolve(
                next_requirements,
                next_forbidden,
                next_assignments,
                next_external,
            )

            if result is not None:
                return result

        return None

    return resolve(
        requirements,
        forbidden,
        assignments,
        external,
    )

def solve_platforms(mod_versions, selected):
    if not selected:
        return []

    platform_releases = {}

    for project_id in selected:
        releases = mod_versions[project_id]

        for release in releases:
            if release["status"] != "listed":
                continue

            for minecraft_version in release["game_versions"]:
                for loader in release["loaders"]:
                    platform = (minecraft_version, loader)

                    if platform not in platform_releases:
                        platform_releases[platform] = {}

                    if project_id not in platform_releases[platform]:
                        platform_releases[platform][project_id] = []

                    platform_releases[platform][project_id].append(release)

    results = []

    for (minecraft_version, loader), project_releases in platform_releases.items():
        compatible = []
        incompatible = []

        for project_id in selected:
            if project_id in project_releases:
                compatible.append(project_id)
            else:
                incompatible.append(project_id)

        results.append({
            "minecraft_version": minecraft_version,
            "loader": loader,
            "compatible": compatible,
            "incompatible": incompatible,
            "releases": project_releases,
        })

    return results

def handle_solve():
    selected = list(selected_view.get_children())

    if not selected:
        print("No mods selected")
        return

    solve_button.config(state=tk.DISABLED)

    def worker():
        try:
            for project_id in selected:
                if project_id not in mod_versions:
                    mod_versions[project_id] = get_modrinth_versions(project_id)

            results = solve_platforms(mod_versions, selected)
        except Exception as error:
            root.after(0, lambda error=error: solve_failed(error))
            return

        root.after(0, lambda: display_results(results))

    threading.Thread(target=worker, daemon=True).start()

def solve_failed(error):
    solve_button.config(state=tk.NORMAL)
    print(f"Solve failed: {error}")

def display_results(results):
    solve_button.config(state=tk.NORMAL)

    for item in results_view_tree.get_children():
        results_view_tree.delete(item)

    result_data.clear()

    selected_count = len(selected_view.get_children())

    if not results:
        result_status.set("No compatible platforms found")
        return

    best = max(len(result["compatible"]) for result in results)

    result_status.set(
        f"{best}/{selected_count} mods supported by best configuration"
    )

    results = sorted(
        results,
        key=lambda result: (
            len(result["compatible"]),
            result["minecraft_version"],
            result["loader"],
        ),
        reverse=True,
    )

    for index, result in enumerate(results):
        iid = str(index)
        result_data[iid] = result

        compatible_count = len(result["compatible"])
        missing_count = len(result["incompatible"])

        results_view_tree.insert(
            "",
            tk.END,
            iid=iid,
            values=(
                result["minecraft_version"],
                result["loader"],
                f"{compatible_count}/{selected_count}",
                missing_count,
            ),
        )
        
def resolution_failed(generation, error):
    if generation != resolution_generation:
        return

    set_details(f"Resolution failed:\n\n{error}")

def get_mod_title(project_id):
    if project_id in mods:
        return mods[project_id]["title"]

    if project_id in mod_projects_by_id:
        return mod_projects_by_id[project_id]["title"]

    return project_id

def display_resolution(generation, result, resolution):
    global current_resolution

    if generation != resolution_generation:
        return

    current_resolution = None
    download_button.config(state=tk.DISABLED)
    export_button.config(state=tk.NORMAL if resolution is not None else tk.DISABLED)

    lines = [
        f"{result['minecraft_version']} + {result['loader']}",
        "",
    ]

    if resolution is None:
        lines.append("No valid release combination exists.")
    else:
        current_resolution = {
            "minecraft_version": result["minecraft_version"],
            "loader": result["loader"],
            "releases": resolution["releases"],
            "external": resolution["external"],
        }

        download_button.config(state=tk.NORMAL)
        lines.append("Resolved:")

        for project_id, release in resolution["releases"].items():
            file = get_primary_file(release)

            lines.append(
                f"  {get_mod_title(project_id)} {release['version_number']}"
            )
            lines.append(
                f"    {file['filename']} ({file['size']:,} bytes)"
            )

        if resolution["external"]:
            lines.append("")
            lines.append("External dependencies:")

            for dependency in resolution["external"]:
                file_name = dependency.get("file_name")

                if file_name is not None:
                    lines.append(f"  {file_name}")
                else:
                    lines.append("  Unknown external dependency")

    if result["incompatible"]:
        lines.append("")
        lines.append("Incompatible selected mods:")

        for project_id in result["incompatible"]:
            lines.append(f"  {get_mod_title(project_id)}")

    set_details("\n".join(lines))

def set_details(text):
    details.config(state=tk.NORMAL)
    details.delete("1.0", tk.END)
    details.insert(tk.END, text)
    details.config(state=tk.DISABLED)

def get_primary_file(release):
    for file in release["files"]:
        if file["primary"]:
            return file

    if release["files"]:
        return release["files"][0]

    raise ValueError(
        f"Release {release['id']} has no files"
    )

def file_matches(path, expected_sha512):
    if expected_sha512 is None or not os.path.isfile(path):
        return False

    hasher = hashlib.sha512()

    with open(path, "rb") as file:
        while True:
            chunk = file.read(1024 * 1024)

            if not chunk:
                break

            hasher.update(chunk)

    return hasher.hexdigest() == expected_sha512

def download_file(url, path, expected_sha512, progress_callback):
    if file_matches(path, expected_sha512):
        return

    temporary_path = f"{path}.part"
    hasher = hashlib.sha512()
    downloaded = 0

    try:
        with open_url(url, timeout=30) as response:
            total = response.headers.get("Content-Length")
            total = int(total) if total is not None else None

            with open(temporary_path, "wb") as file:
                while True:
                    chunk = response.read(1024 * 1024)

                    if not chunk:
                        break

                    file.write(chunk)
                    hasher.update(chunk)
                    downloaded += len(chunk)

                    if total is not None:
                        progress_callback(downloaded, total)

        actual_sha512 = hasher.hexdigest()

        if expected_sha512 is not None and actual_sha512 != expected_sha512:
            raise ValueError(
                f"SHA-512 mismatch for {os.path.basename(path)}"
            )

        os.replace(temporary_path, path)

    except Exception:
        if os.path.exists(temporary_path):
            os.remove(temporary_path)

        raise

def handle_download():
    if current_resolution is None:
        return

    directory = filedialog.askdirectory(
        title="Choose Modpack Directory",
    )

    if not directory:
        return

    download_button.config(state=tk.DISABLED)

    resolution = current_resolution

    def worker():
        try:
            mods_directory = os.path.join(directory, "mods")
            os.makedirs(mods_directory, exist_ok=True)

            releases = list(resolution["releases"].items())

            for index, (project_id, release) in enumerate(releases):
                title = get_mod_title(project_id)
                file = get_primary_file(release)

                filename = os.path.basename(file["filename"])
                path = os.path.join(mods_directory, filename)
                expected_sha512 = file["hashes"].get("sha512")

                root.after(
                    0,
                    lambda title=title: download_status.set(
                        f"Downloading {title}..."
                    ),
                )

                download_file(
                    file["url"],
                    path,
                    expected_sha512,
                    lambda downloaded, total, title=title, index=index: root.after(
                        0,
                        lambda: update_download_progress(
                            index,
                            len(releases),
                            title,
                            downloaded,
                            total,
                        ),
                    ),
                )

            root.after(0, download_complete)

        except Exception as error:
            root.after(0, lambda error=error: download_failed(error))

    threading.Thread(target=worker, daemon=True).start()

def update_download_progress(index, total_count, name, downloaded, total):
    if total:
        percent = int(downloaded * 100 / total)

        download_status.set(
            f"Downloading {name} ({percent}%) "
            f"[{index + 1}/{total_count}]"
        )
    else:
        download_status.set(
            f"Downloading {name} [{index + 1}/{total_count}]"
        )

def download_complete():
    download_button.config(state=tk.NORMAL)
    download_status.set("Download complete")

def download_failed(error):
    download_button.config(state=tk.NORMAL)
    download_status.set(f"Download failed: {error}")

def handle_result_selected(*args):
    global resolution_generation
    global current_resolution

    export_button.config(state=tk.DISABLED)

    selection = results_view_tree.selection()

    if not selection:
        current_resolution = None
        download_button.config(state=tk.DISABLED)
        set_details("")
        return

    result = result_data[selection[0]]
    resolution_generation += 1
    generation = resolution_generation
    current_resolution = None

    download_button.config(state=tk.DISABLED)

    set_details(
        f"{result['minecraft_version']} + {result['loader']}\n\n"
        "Resolving..."
    )

    def worker():
        try:
            resolution = resolve_releases(
                result["minecraft_version"],
                result["loader"],
                result["compatible"],
            )
        except Exception as error:
            root.after(0, lambda error=error: resolution_failed(generation, error))
            return

        root.after(
            0,
            lambda: display_resolution(
                generation,
                result,
                resolution,
            ),
        )

    threading.Thread(target=worker, daemon=True).start()

root = tk.Tk()
root.title("Minecraft Modpack Maker")
root.geometry("900x700")

DARK_BG = "#0d1117"
DARK_PANEL = "#161b22"
DARK_INPUT = "#010409"
DARK_FG = "#e6edf3"
DARK_MUTED = "#8b949e"
DARK_SELECT = "#1f6feb"
DARK_BORDER = "#30363d"
DARK_HOVER = "#21262d"

root.configure(bg=DARK_BG)

style = ttk.Style()
style.theme_use("clam")

style.configure("TFrame", background=DARK_BG)
style.configure("TPanedwindow", background=DARK_BG)
style.configure("TLabel", background=DARK_BG, foreground=DARK_FG)
style.configure("TButton", background=DARK_PANEL, foreground=DARK_FG, bordercolor=DARK_BORDER)
style.configure("TEntry", fieldbackground=DARK_INPUT, foreground=DARK_FG, bordercolor=DARK_BORDER)
style.configure("Treeview", background=DARK_INPUT, fieldbackground=DARK_INPUT, foreground=DARK_FG, bordercolor=DARK_BORDER)
style.configure("Treeview.Heading", background=DARK_PANEL, foreground=DARK_FG, bordercolor=DARK_BORDER)
style.configure("Sash", sashthickness=6, background=DARK_BORDER)

style.map("TButton", background=[("active", DARK_HOVER), ("pressed", DARK_SELECT)], foreground=[("disabled", DARK_MUTED)])
style.map("Treeview", background=[("selected", DARK_SELECT)], foreground=[("selected", "#ffffff")])
style.map("Treeview.Heading", background=[("active", DARK_HOVER)])

search_var = tk.StringVar()

search_entry = ttk.Entry(root, textvariable=search_var)
search_entry.focus_set()
search_entry.pack(padx=10, pady=10, fill=tk.X)

mods = {}
mod_versions = {}
mod_versions_by_id = {}
mod_projects_by_id = {}
resolution_generation = 0
current_resolution = None

main_view = ttk.PanedWindow(root, orient=tk.VERTICAL)
main_view.pack(padx=10, pady=10, expand=True, fill=tk.BOTH)

mods_view = ttk.PanedWindow(main_view, orient=tk.HORIZONTAL)
main_view.add(mods_view, weight=3)

available_frame = ttk.Frame(mods_view)
mods_view.add(available_frame, weight=1)

available_label = ttk.Label(available_frame, text="Available Mods")
available_label.pack(padx=5, pady=(0, 5), anchor=tk.W)

available_view = ttk.Treeview(available_frame, columns=("title", "author", "downloads"), show="headings")
available_view.pack(expand=True, fill=tk.BOTH)

available_view.heading("title", text="Title")
available_view.heading("author", text="Author")
available_view.heading("downloads", text="Downloads")

selected_frame = ttk.Frame(mods_view)
mods_view.add(selected_frame, weight=1)

selected_label = ttk.Label(selected_frame, text="Selected Mods")
selected_label.pack(padx=5, pady=(0, 5), anchor=tk.W)

selected_view = ttk.Treeview(selected_frame, columns=("title", "author", "downloads"), show="headings")
selected_view.pack(expand=True, fill=tk.BOTH)

selected_view.heading("title", text="Title")
selected_view.heading("author", text="Author")
selected_view.heading("downloads", text="Downloads")

button_frame = ttk.Frame(root)
button_frame.pack(padx=10, pady=(0, 10), fill=tk.X)

add_button = ttk.Button(button_frame, text="Add", command=handle_add)
add_button.pack(side=tk.LEFT)

remove_button = ttk.Button(button_frame, text="Remove", command=handle_remove)
remove_button.pack(side=tk.LEFT, padx=(5, 0))

solve_button = ttk.Button(button_frame, text="Solve", command=handle_solve)
solve_button.pack(side=tk.RIGHT)

download_button = ttk.Button(button_frame, text="Download", command=handle_download, state=tk.DISABLED)
download_button.pack(side=tk.RIGHT, padx=(5, 0))

download_status = tk.StringVar()
download_status_label = ttk.Label(button_frame, textvariable=download_status)
download_status_label.pack(side=tk.RIGHT, padx=10)

export_button = ttk.Button(button_frame, text="Export .mrpack", command=handle_export, state=tk.DISABLED)
export_button.pack(side=tk.RIGHT, padx=(5, 0))

results_view = ttk.PanedWindow(main_view, orient=tk.HORIZONTAL)
main_view.add(results_view, weight=2)

results_frame = ttk.Frame(results_view)
results_view.add(results_frame, weight=1)

result_status = tk.StringVar()
result_status_label = ttk.Label(results_frame, textvariable=result_status)
result_status_label.pack(padx=5, pady=(0, 5), anchor=tk.W)

results_view_tree = ttk.Treeview(results_frame, columns=("minecraft", "loader", "compatible", "missing"), show="headings")
results_view_tree.pack(expand=True, fill=tk.BOTH)

results_view_tree.heading("minecraft", text="Minecraft")
results_view_tree.heading("loader", text="Loader")
results_view_tree.heading("compatible", text="Compatible")
results_view_tree.heading("missing", text="Incompatible")

results_view_tree.column("minecraft", width=100)
results_view_tree.column("loader", width=100)
results_view_tree.column("compatible", width=100)
results_view_tree.column("missing", width=100)

details_frame = ttk.Frame(results_view)
results_view.add(details_frame, weight=1)

details_label = ttk.Label(details_frame, text="Details")
details_label.pack(padx=5, pady=(0, 5), anchor=tk.W)

details = tk.Text(
    details_frame,
    height=10,
    bg=DARK_INPUT,
    fg=DARK_FG,
    insertbackground=DARK_FG,
    selectbackground=DARK_SELECT,
    selectforeground="#ffffff",
    relief=tk.FLAT,
    borderwidth=0,
    state=tk.DISABLED,
    takefocus=False,
    cursor="arrow",
)

details.pack(expand=True, fill=tk.BOTH)

result_data = {}

results_view_tree.bind("<<TreeviewSelect>>", handle_result_selected)

search_job = None
search_generation = 0

search_var.trace_add("write", on_search_changed)

def select_all_search(event):
    search_entry.select_range(0, tk.END)
    return "break"

def clear_search(event):
    search_var.set("")
    return "break"

search_entry.bind("<Control-a>", select_all_search)
search_entry.bind("<Escape>", clear_search)
search_entry.bind("<Return>", handle_add)

available_view.bind("<Return>", handle_add)
available_view.bind("<Double-1>", handle_add)

selected_view.bind("<Delete>", handle_remove)

root.bind("<Control-Return>", lambda event: handle_solve())

update_tree()

root.mainloop()