#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Validador / reorganizador de proyectos Android subidos "aplanados" a la raíz.
Uso (desde la raíz del repo): python3 .github/scripts/validator.py
"""
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from xml.sax.saxutils import quoteattr

ROOT = Path.cwd().resolve()
APP = ROOT / "app"
MAIN = APP / "src" / "main"
JAVA = MAIN / "java"
RES = MAIN / "res"
ASSETS = MAIN / "assets"
JNI = MAIN / "jniLibs"

# AGP 8.6.1 es la última versión compatible con Gradle 8.7
AGP, KOTLIN, KSP, GRADLE = "8.6.1", "2.0.21", "2.0.21-1.0.28", "8.7"
MIN_SDK, TARGET_SDK, COMPILE_SDK = 23, 36, 36
ANDROID_NS = "http://schemas.android.com/apk/res/android"
TOOLS_NS = "http://schemas.android.com/tools"

SKIP_ROOT = {".git", ".github", ".gradle", ".idea", "build", "gradle", "app",
             "node_modules", "__MACOSX"}
RES_DIR_RE = re.compile(
    r"^(layout|values|drawable|mipmap|menu|xml|anim|animator|raw|font|color|"
    r"navigation|transition|interpolator)(-\w+)*$")
PKG_RE = re.compile(r"^\s*package\s+([\w.]+)", re.M)
IMPORT_RE = re.compile(r"^\s*import\s+(?:static\s+)?([\w.]+)", re.M)
R_IMPORT_RE = re.compile(r"^\s*import\s+([\w.]+)\.R(?:[.;\s]|$)", re.M)
ACT_KT = re.compile(
    r"class\s+(\w+)\s*(?:\((?:[^()]|\([^()]*\))*\))?\s*:\s*[^{\n]*?"
    r"\b(?:ComponentActivity|AppCompatActivity|FragmentActivity|Activity)\b")
ACT_JAVA = re.compile(
    r"class\s+(\w+)\s+extends\s+"
    r"(?:ComponentActivity|AppCompatActivity|FragmentActivity|Activity)\b")
IMG_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
AUDIO_EXT = {".mp3", ".ogg", ".wav"}
DRAWABLE_TAGS = {"vector", "shape", "layer-list", "ripple", "bitmap", "animated-vector",
                 "level-list", "inset", "animation-list", "animated-selector",
                 "nine-patch", "transition"}
XML_CFG_TAGS = {"paths", "network-security-config", "data-extraction-rules",
                "full-backup-content", "locale-config", "device-admin",
                "accessibility-service", "PreferenceScreen", "preference-headers",
                "searchable", "appwidget-provider", "shortcuts"}

# prefijo de import -> dependencias Gradle
LIBS = [
    ("androidx.appcompat", ["implementation 'androidx.appcompat:appcompat:1.7.0'"]),
    ("com.google.android.material", ["implementation 'com.google.android.material:material:1.12.0'"]),
    ("androidx.constraintlayout", ["implementation 'androidx.constraintlayout:constraintlayout:2.1.4'"]),
    ("androidx.recyclerview", ["implementation 'androidx.recyclerview:recyclerview:1.3.2'"]),
    ("androidx.cardview", ["implementation 'androidx.cardview:cardview:1.0.0'"]),
    ("androidx.viewpager2", ["implementation 'androidx.viewpager2:viewpager2:1.1.0'"]),
    ("androidx.swiperefreshlayout", ["implementation 'androidx.swiperefreshlayout:swiperefreshlayout:1.1.0'"]),
    ("androidx.fragment", ["implementation 'androidx.fragment:fragment-ktx:1.8.5'"]),
    ("androidx.activity", ["implementation 'androidx.activity:activity-ktx:1.9.3'"]),
    ("androidx.lifecycle", ["implementation 'androidx.lifecycle:lifecycle-runtime-ktx:2.8.7'",
                            "implementation 'androidx.lifecycle:lifecycle-viewmodel-ktx:2.8.7'",
                            "implementation 'androidx.lifecycle:lifecycle-livedata-ktx:2.8.7'"]),
    ("androidx.navigation.fragment", ["implementation 'androidx.navigation:navigation-fragment-ktx:2.8.4'",
                                      "implementation 'androidx.navigation:navigation-ui-ktx:2.8.4'"]),
    ("androidx.navigation.compose", ["implementation 'androidx.navigation:navigation-compose:2.8.4'"]),
    ("androidx.datastore", ["implementation 'androidx.datastore:datastore-preferences:1.1.1'"]),
    ("androidx.work", ["implementation 'androidx.work:work-runtime-ktx:2.9.1'"]),
    ("androidx.preference", ["implementation 'androidx.preference:preference-ktx:1.2.1'"]),
    ("kotlinx.coroutines", ["implementation 'org.jetbrains.kotlinx:kotlinx-coroutines-android:1.9.0'"]),
    ("com.google.gson", ["implementation 'com.google.code.gson:gson:2.11.0'"]),
    ("okhttp3", ["implementation 'com.squareup.okhttp3:okhttp:4.12.0'"]),
    ("retrofit2", ["implementation 'com.squareup.retrofit2:retrofit:2.11.0'",
                   "implementation 'com.squareup.retrofit2:converter-gson:2.11.0'"]),
    ("org.osmdroid", ["implementation 'org.osmdroid:osmdroid-android:6.1.20'"]),
    ("com.google.android.gms.location", ["implementation 'com.google.android.gms:play-services-location:21.3.0'"]),
    ("coil.", ["implementation 'io.coil-kt:coil-compose:2.7.0'"]),
]
COMPOSE_DEPS = [
    "implementation platform('androidx.compose:compose-bom:2024.10.01')",
    "implementation 'androidx.compose.ui:ui'",
    "implementation 'androidx.compose.ui:ui-graphics'",
    "implementation 'androidx.compose.ui:ui-tooling-preview'",
    "implementation 'androidx.compose.material3:material3'",
    "implementation 'androidx.activity:activity-compose:1.9.3'",
    "implementation 'androidx.lifecycle:lifecycle-runtime-compose:2.8.7'",
    "implementation 'androidx.lifecycle:lifecycle-viewmodel-compose:2.8.7'",
    "debugImplementation 'androidx.compose.ui:ui-tooling'",
]

# ----------------------------------------------------------------- plantillas
SETTINGS_TPL = """pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}
dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.PREFER_SETTINGS)
    repositories {
        google()
        mavenCentral()
    }
}
rootProject.name = "@@NAME@@"
include ':app'
"""

PROPERTIES_TPL = """org.gradle.jvmargs=-Xmx3g -Dfile.encoding=UTF-8
org.gradle.parallel=true
org.gradle.caching=true
android.useAndroidX=true
android.nonTransitiveRClass=true
android.suppressUnsupportedCompileSdk=36
kotlin.code.style=official
"""

ROOT_GRADLE_TPL = """// Nivel raíz: solo declara versiones de plugins (se aplican en app/build.gradle)
plugins {
    id 'com.android.application' version '@@AGP@@' apply false
    id 'org.jetbrains.kotlin.android' version '@@KOTLIN@@' apply false
    id 'org.jetbrains.kotlin.plugin.compose' version '@@KOTLIN@@' apply false
    id 'com.google.devtools.ksp' version '@@KSP@@' apply false
}
"""

APP_GRADLE_TPL = """plugins {
@@PLUGINS@@
}

android {
    namespace '@@PKG@@'
    compileSdk @@COMPILE@@

    defaultConfig {
        applicationId '@@PKG@@'
        minSdk @@MIN@@
        targetSdk @@TARGET@@
        versionCode 1
        versionName '1.0'

        // Solo ABIs de dispositivos reales: Android 6+ en 32 y 64 bits
        ndk {
            abiFilters 'arm64-v8a', 'armeabi-v7a'
        }
    }

    buildTypes {
        debug {
            debuggable true
        }
        release {
            minifyEnabled false
            proguardFiles getDefaultProguardFile('proguard-android-optimize.txt'), 'proguard-rules.pro'
        }
    }

    compileOptions {
        sourceCompatibility JavaVersion.VERSION_17
        targetCompatibility JavaVersion.VERSION_17
    }
@@KOTLINOPTS@@@@FEATURES@@
    packaging {
        resources {
            excludes += '/META-INF/{AL2.0,LGPL2.1}'
        }
    }
}

dependencies {
@@DEPS@@
}
"""

GITIGNORE_TPL = """*.iml
.gradle/
.idea/
/local.properties
.DS_Store
build/
/captures
.externalNativeBuild/
.cxx/
"""


# ------------------------------------------------------------------- helpers
def log(msg):
    print(f"[validator] {msg}", flush=True)


def rel(p):
    try:
        return Path(p).resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(p)


def read(p):
    return Path(p).read_text(encoding="utf-8", errors="ignore")


def write(p, text):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8", newline="\n")


def write_if_missing(p, text):
    if Path(p).exists():
        return False
    write(p, text)
    log(f"CREADO    {rel(p)}")
    return True


def move(src, dst):
    src, dst = Path(src), Path(dst)
    a, b = rel(src), rel(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        log(f"  ! se sobrescribe destino existente: {b}")
        dst.unlink()
    shutil.move(str(src), str(dst))
    log(f"  mover  {a}  ->  {b}")


def clean(name):
    """Nombre de recurso válido: [a-z0-9_] (conserva .9.png)."""
    if name.lower().endswith(".9.png"):
        stem, ext = name[:-6], ".9.png"
    else:
        stem, ext = os.path.splitext(name)
        ext = ext.lower()
    s = re.sub(r"[^a-z0-9_]", "_", stem.lower())
    if not s or s[0].isdigit():
        s = "r_" + s
    return s + ext


def has(imports, prefix):
    return any(i.startswith(prefix) for i in imports)


# ---------------------------------------------------------------- 1. ZIP
def extract_zips():
    for z in sorted(ROOT.glob("*.zip")):
        log(f"Extrayendo {z.name}")
        try:
            with zipfile.ZipFile(z) as zf:
                names = [n for n in zf.namelist()
                         if not n.startswith("__MACOSX") and not n.endswith(".DS_Store")]
                tops = {n.split("/")[0] for n in names if n.strip("/")}
                strip = len(tops) == 1 and all(
                    n.startswith(next(iter(tops)) + "/") for n in names)
                for n in names:
                    if n.endswith("/"):
                        continue
                    parts = n.split("/")
                    if strip:
                        parts = parts[1:]
                    if not parts or parts[0] in (".git", ".github"):
                        continue
                    target = ROOT.joinpath(*parts).resolve()
                    if ROOT not in target.parents:  # anti zip-slip
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(n) as s, open(target, "wb") as d:
                        shutil.copyfileobj(s, d)
            z.unlink()
        except zipfile.BadZipFile:
            log(f"  ! {z.name} está corrupto, se ignora")


# ------------------------------------------------------- 2. REUBICACIÓN
def list_loose_files():
    out = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        if Path(dirpath) == ROOT:
            dirnames[:] = [d for d in dirnames if d not in SKIP_ROOT]
        elif ".git" in dirnames:
            dirnames.remove(".git")
        out += [Path(dirpath) / f for f in filenames]
    return out


def xml_kind(text):
    t = re.sub(r"<\?xml.*?\?>|<!--.*?-->", "", text, flags=re.S)
    m = re.search(r"<\s*([A-Za-z_][\w.\-]*)", t)
    if not m:
        return None
    tag = m.group(1)
    if tag == "manifest":
        return "manifest"
    if tag == "resources":
        return "values"
    if tag in ("menu", "navigation"):
        return tag
    if tag == "font-family":
        return "font"
    if tag == "adaptive-icon":
        return "mipmap-anydpi-v26"
    if tag in ("objectAnimator", "valueAnimator", "animator"):
        return "animator"
    if tag == "set":
        return "animator" if re.search(r"objectAnimator|valueAnimator", t) else "anim"
    if tag in ("translate", "alpha"):
        return "anim"
    if tag in ("rotate", "scale"):
        return "drawable" if "android:drawable" in t else "anim"
    if tag == "selector":
        if "android:drawable" in t:
            return "drawable"
        return "color" if "android:color" in t else "drawable"
    if tag in DRAWABLE_TAGS:
        return "drawable"
    if tag in XML_CFG_TAGS:
        return "xml"
    if tag[0].isupper() or "." in tag or tag in ("merge", "layout", "include", "view", "fragment"):
        return "layout"
    return None


def target_for(f):
    name = f.name
    if name == "AndroidManifest.xml":
        return MAIN / name
    if name in ("proguard-rules.pro", "consumer-rules.pro", "google-services.json"):
        return APP / name
    parts = f.relative_to(ROOT).parts[:-1]
    if any(p in ("test", "androidTest") for p in parts):
        return None
    ext = f.suffix.lower()
    if ext in (".kt", ".java"):
        m = PKG_RE.search(read(f))
        pkg = Path(*m.group(1).split(".")) if m else Path()
        return JAVA / pkg / name
    for i, part in enumerate(parts):  # ancestro más externo reconocible
        if RES_DIR_RE.match(part):
            return RES.joinpath(*parts[i:], clean(name))
        if part == "assets":
            return ASSETS.joinpath(*parts[i + 1:], name)
        if part == "jniLibs":
            return JNI.joinpath(*parts[i + 1:], name)
    if parts:  # carpeta desconocida: no se toca
        return None
    # archivos sueltos en la raíz
    if ext in IMG_EXT:
        return RES / "drawable-nodpi" / clean(name)
    if ext in (".ttf", ".otf"):
        return RES / "font" / clean(name)
    if ext in AUDIO_EXT:
        return RES / "raw" / clean(name)
    if ext == ".xml":
        kind = xml_kind(read(f))
        if kind == "manifest":
            return MAIN / "AndroidManifest.xml"
        if kind:
            return RES / kind / clean(name)
    return None


def relocate():
    moved = 0
    for f in list_loose_files():
        if not f.exists():
            continue
        dst = target_for(f)
        if dst and dst.resolve() != f.resolve():
            move(f, dst)
            moved += 1
    log(f"{moved} archivo(s) reubicado(s)")


def place_gradle_files():
    """Un build.gradle suelto en la raíz con bloque android{} es el del módulo app."""
    for name in ("build.gradle", "build.gradle.kts"):
        f = ROOT / name
        if f.exists() and re.search(r"^\s*android\s*\{", read(f), re.M):
            if (APP / name).exists():
                log(f"  ! {name} de raíz parece de app, pero app/{name} ya existe")
            else:
                move(f, APP / name)


def prune_empty():
    for dirpath, _, _ in os.walk(ROOT, topdown=False):
        p = Path(dirpath)
        if p == ROOT or p.relative_to(ROOT).parts[0] in SKIP_ROOT:
            continue
        try:
            p.rmdir()
        except OSError:
            pass


# --------------------------------------------------------------- 3. ANÁLISIS
def scan_sources():
    files = []
    for base in (JAVA, MAIN / "kotlin"):
        if base.exists():
            files += [p for p in base.rglob("*") if p.suffix in (".kt", ".java")]
    return {p: read(p) for p in files}


def detect_package(texts):
    mf = MAIN / "AndroidManifest.xml"
    if mf.exists():
        m = re.search(r'<manifest[^>]*\spackage\s*=\s*"([\w.]+)"', read(mf))
        if m:
            return m.group(1)
    alltext = "\n".join(texts.values())
    rs = Counter(p for p in R_IMPORT_RE.findall(alltext) if p != "android")
    if rs:
        return rs.most_common(1)[0][0]
    pkgs = []
    for p, t in texts.items():
        m = PKG_RE.search(t)
        if m:
            if p.stem == "MainActivity":
                return m.group(1)
            pkgs.append(m.group(1))
    if pkgs:
        common = os.path.commonprefix([x.split(".") for x in pkgs])
        if len(common) >= 2:
            return ".".join(common)
        return Counter(pkgs).most_common(1)[0][0]
    return "com.example.app"


def find_activity(texts):
    found = []
    for p, t in sorted(texts.items(), key=lambda x: str(x[0])):
        rx = ACT_KT if p.suffix == ".kt" else ACT_JAVA
        pk = PKG_RE.search(t)
        for m in rx.finditer(t):
            if re.search(r"abstract\s+$", t[:m.start()]):
                continue
            found.append((m.group(1), pk.group(1) if pk else ""))
    if not found:
        return None
    cls, pk = next((x for x in found if x[0] == "MainActivity"), found[0])
    return f"{pk}.{cls}" if pk else cls


def build_context(texts):
    imports = set()
    alltext = "\n".join(texts.values())
    for t in texts.values():
        imports.update(IMPORT_RE.findall(t))
    for f in RES.glob("layout*/*.xml"):  # vistas declaradas en XML
        imports.update(re.findall(r"<\s*([a-z]\w*(?:\.\w+)+)", read(f)))
    for f in RES.glob("values*/*.xml"):  # temas AppCompat / Material
        s = read(f)
        if re.search(r"MaterialComponents|Material3", s):
            imports.add("com.google.android.material.theme")
        if "AppCompat" in s:
            imports.add("androidx.appcompat.theme")
    compose = has(imports, "androidx.compose") or has(imports, "androidx.activity.compose")
    kotlin = compose or any(p.suffix == ".kt" for p in texts)
    perms = set(re.findall(r"permission\.([A-Z][A-Z0-9_]+)", alltext))
    if any(i.startswith(("org.osmdroid", "okhttp3", "retrofit2", "java.net.", "coil."))
           for i in imports):
        perms |= {"INTERNET", "ACCESS_NETWORK_STATE"}
    return {
        "imports": imports,
        "compose": compose,
        "kotlin": kotlin,
        "pkg": detect_package(texts),
        "activity": find_activity(texts),
        "perms": sorted(perms),
        "viewbinding": bool(re.search(r"\.databinding\.\w+Binding", alltext)),
        "buildconfig": bool(re.search(r"\bBuildConfig\b", alltext)),
    }


# ------------------------------------------------- 4. ARCHIVOS DE CONFIGURACIÓN
def ensure_settings():
    if (ROOT / "settings.gradle").exists() or (ROOT / "settings.gradle.kts").exists():
        return
    name = os.environ.get("GITHUB_REPOSITORY", "App").split("/")[-1]
    write_if_missing(ROOT / "settings.gradle", SETTINGS_TPL.replace("@@NAME@@", name))


def ensure_properties():
    p = ROOT / "gradle.properties"
    if not p.exists():
        write_if_missing(p, PROPERTIES_TPL)
        return
    txt = read(p)
    add = []
    for key, val in (("android.useAndroidX", "true"),
                     ("android.suppressUnsupportedCompileSdk", "36")):
        if not re.search(rf"^\s*{re.escape(key)}\s*=", txt, re.M):
            add.append(f"{key}={val}")
    if add:
        if not txt.endswith("\n"):
            txt += "\n"
        write(p, txt + "\n".join(add) + "\n")
        log(f"ACTUALIZADO gradle.properties (+ {', '.join(add)})")


def ensure_root_gradle():
    if (ROOT / "build.gradle").exists() or (ROOT / "build.gradle.kts").exists():
        return
    write_if_missing(ROOT / "build.gradle",
                     ROOT_GRADLE_TPL.replace("@@AGP@@", AGP)
                     .replace("@@KOTLIN@@", KOTLIN).replace("@@KSP@@", KSP))


def build_deps(ctx):
    imp, kt = ctx["imports"], ctx["kotlin"]
    deps = ["implementation 'androidx.core:core-ktx:1.13.1'"]
    if ctx["compose"]:
        deps += COMPOSE_DEPS
        if has(imp, "androidx.compose.material.icons"):
            deps.append("implementation 'androidx.compose.material:material-icons-extended'")
        if any(i.startswith("androidx.compose.material.")
               and not i.startswith("androidx.compose.material.icons") for i in imp):
            deps.append("implementation 'androidx.compose.material:material'")
        if has(imp, "androidx.compose.runtime.livedata"):
            deps.append("implementation 'androidx.compose.runtime:runtime-livedata'")
    for prefix, lines in LIBS:
        if has(imp, prefix):
            deps += lines
    if has(imp, "androidx.room"):
        deps.append("implementation 'androidx.room:room-runtime:2.6.1'")
        if kt:
            deps += ["implementation 'androidx.room:room-ktx:2.6.1'",
                     "ksp 'androidx.room:room-compiler:2.6.1'"]
        else:
            deps.append("annotationProcessor 'androidx.room:room-compiler:2.6.1'")
    seen, out = set(), []
    for d in deps:
        if d not in seen:
            seen.add(d)
            out.append(d)
    return "\n".join("    " + d for d in out)


def ensure_app_gradle(ctx):
    if (APP / "build.gradle").exists() or (APP / "build.gradle.kts").exists():
        return
    plugins = ["    id 'com.android.application'"]
    if ctx["kotlin"]:
        plugins.append("    id 'org.jetbrains.kotlin.android'")
    if ctx["compose"]:
        plugins.append("    id 'org.jetbrains.kotlin.plugin.compose'")
    if ctx["kotlin"] and has(ctx["imports"], "androidx.room"):
        plugins.append("    id 'com.google.devtools.ksp'")
    kotlinopts = "\n    kotlinOptions {\n        jvmTarget = '17'\n    }\n" if ctx["kotlin"] else ""
    feats = []
    if ctx["compose"]:
        feats.append("compose true")
    if ctx["viewbinding"]:
        feats.append("viewBinding true")
    if ctx["buildconfig"]:
        feats.append("buildConfig true")
    features = ("\n    buildFeatures {\n" + "".join(f"        {x}\n" for x in feats) + "    }\n"
                if feats else "")
    text = (APP_GRADLE_TPL.replace("@@PLUGINS@@", "\n".join(plugins))
            .replace("@@PKG@@", ctx["pkg"]).replace("@@COMPILE@@", str(COMPILE_SDK))
            .replace("@@MIN@@", str(MIN_SDK)).replace("@@TARGET@@", str(TARGET_SDK))
            .replace("@@KOTLINOPTS@@", kotlinopts).replace("@@FEATURES@@", features)
            .replace("@@DEPS@@", build_deps(ctx)))
    write_if_missing(APP / "build.gradle", text)
    write_if_missing(APP / "proguard-rules.pro", "# Reglas ProGuard/R8 (vacío)\n")


def ensure_namespace(pkg):
    """AGP 8 exige 'namespace'. Si el build.gradle del usuario no lo trae, se agrega."""
    for name, fmt in (("build.gradle", "    namespace '%s'"),
                      ("build.gradle.kts", '    namespace = "%s"')):
        f = APP / name
        if not f.exists():
            continue
        txt = read(f)
        if re.search(r"\bnamespace\b", txt):
            return
        new = re.sub(r"^(\s*android\s*\{)", lambda m: m.group(1) + "\n" + fmt % pkg,
                     txt, count=1, flags=re.M)
        if new != txt:
            write(f, new)
            log(f"ACTUALIZADO {name} (+ namespace {pkg})")
        return


def resolve_theme(ctx):
    for f in sorted(RES.glob("values*/*.xml")):
        for name, _ in re.findall(r'<style\s+name="([\w.]+)"\s+parent="([^"]*)"', read(f)):
            if "theme" in name.lower():
                return "@style/" + name
    imp = ctx["imports"]
    if has(imp, "com.google.android.material"):
        parent = "Theme.MaterialComponents.DayNight.NoActionBar"
    elif has(imp, "androidx.appcompat"):
        parent = "Theme.AppCompat.DayNight.NoActionBar"
    else:
        return "@android:style/Theme.Material.Light.NoActionBar"
    write_if_missing(RES / "values" / "themes_autogen.xml",
                     '<?xml version="1.0" encoding="utf-8"?>\n<resources>\n'
                     f'    <style name="Theme.AutoApp" parent="{parent}" />\n</resources>\n')
    return "@style/Theme.AutoApp"


def ensure_manifest(ctx):
    mf = MAIN / "AndroidManifest.xml"
    if mf.exists():
        return
    app_name = any('name="app_name"' in read(f) for f in RES.glob("values*/*.xml"))
    label = "@string/app_name" if app_name else os.environ.get(
        "GITHUB_REPOSITORY", "App").split("/")[-1]
    icon = ""
    if any(RES.glob("mipmap*/ic_launcher.*")):
        icon = '        android:icon="@mipmap/ic_launcher"\n'
        if any(RES.glob("mipmap*/ic_launcher_round.*")):
            icon += '        android:roundIcon="@mipmap/ic_launcher_round"\n'
    L = ['<?xml version="1.0" encoding="utf-8"?>',
         '<manifest xmlns:android="http://schemas.android.com/apk/res/android">', ""]
    L += [f'    <uses-permission android:name="android.permission.{p}" />' for p in ctx["perms"]]
    L += ["", "    <application",
          "        android:allowBackup=\"true\"",
          f"        android:label={quoteattr(label)}"]
    if icon:
        L.append(icon.rstrip("\n"))
    L += ['        android:supportsRtl="true"',
          f'        android:theme="{resolve_theme(ctx)}">']
    if ctx["activity"]:
        L += ["        <activity",
              f'            android:name="{ctx["activity"]}"',
              '            android:exported="true">',
              "            <intent-filter>",
              '                <action android:name="android.intent.action.MAIN" />',
              '                <category android:name="android.intent.category.LAUNCHER" />',
              "            </intent-filter>",
              "        </activity>"]
    else:
        log("  ! no se encontró ninguna Activity; el manifest queda sin actividad")
    L += ["    </application>", "</manifest>", ""]
    write_if_missing(mf, "\n".join(L))


def fix_manifest():
    """Auditoría: android:exported obligatorio (targetSdk 31+) y sin atributo package."""
    mf = MAIN / "AndroidManifest.xml"
    if not mf.exists():
        return
    ET.register_namespace("android", ANDROID_NS)
    ET.register_namespace("tools", TOOLS_NS)
    try:
        tree = ET.parse(mf)
    except ET.ParseError as e:
        log(f"  ! AndroidManifest.xml mal formado: {e}")
        return
    root, changed = tree.getroot(), False
    key, nkey = f"{{{ANDROID_NS}}}exported", f"{{{ANDROID_NS}}}name"
    for el in root.iter():
        if el.tag not in ("activity", "activity-alias", "service", "receiver"):
            continue
        is_main = any(a.get(nkey) == "android.intent.action.MAIN" for a in el.iter("action"))
        if is_main and el.get(key) != "true":
            el.set(key, "true")
            changed = True
        elif el.find("intent-filter") is not None and key not in el.attrib:
            el.set(key, "true")
            changed = True
    gradle = next((APP / n for n in ("build.gradle", "build.gradle.kts")
                   if (APP / n).exists()), None)
    if gradle and re.search(r"\bnamespace\b", read(gradle)) and "package" in root.attrib:
        del root.attrib["package"]
        changed = True
    if changed:
        ET.indent(tree)
        tree.write(mf, encoding="utf-8", xml_declaration=True)
        log("ACTUALIZADO AndroidManifest.xml (exported / package)")


# ------------------------------------------------------------- 5. WRAPPER
def make_executable(p):
    p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def ensure_wrapper():
    wdir = ROOT / "gradle" / "wrapper"
    gradlew, jar, props = ROOT / "gradlew", wdir / "gradle-wrapper.jar", wdir / "gradle-wrapper.properties"
    if not (gradlew.exists() and jar.exists() and props.exists()):
        wdir.mkdir(parents=True, exist_ok=True)
        done = False
        if shutil.which("gradle"):  # Gradle 8.7 instalado por el workflow
            tmp = Path(tempfile.mkdtemp())
            r = subprocess.run(["gradle", "wrapper", "--gradle-version", GRADLE,
                                "--distribution-type", "bin", "--no-daemon", "-q"],
                               cwd=tmp, capture_output=True, text=True)
            if r.returncode == 0 and (tmp / "gradlew").exists():
                shutil.copy2(tmp / "gradlew", gradlew)
                if (tmp / "gradlew.bat").exists():
                    shutil.copy2(tmp / "gradlew.bat", ROOT / "gradlew.bat")
                for f in (tmp / "gradle" / "wrapper").glob("*"):
                    shutil.copy2(f, wdir / f.name)
                done = True
            else:
                log(f"  ! 'gradle wrapper' falló: {r.stderr.strip()[:300]}")
        if not done:  # respaldo: descarga directa del tag v8.7.0
            base = f"https://raw.githubusercontent.com/gradle/gradle/v{GRADLE}.0/"
            for remote, local in (("gradlew", gradlew), ("gradlew.bat", ROOT / "gradlew.bat"),
                                  ("gradle/wrapper/gradle-wrapper.jar", jar)):
                with urllib.request.urlopen(base + remote, timeout=60) as r:
                    local.write_bytes(r.read())
            write(props, "distributionBase=GRADLE_USER_HOME\ndistributionPath=wrapper/dists\n"
                  f"distributionUrl=https\\://services.gradle.org/distributions/gradle-{GRADLE}-bin.zip\n"
                  "networkTimeout=10000\nvalidateDistributionUrl=true\n"
                  "zipStoreBase=GRADLE_USER_HOME\nzipStorePath=wrapper/dists\n")
        log("CREADO    Gradle Wrapper (gradlew + gradle/wrapper/*)")
    data = gradlew.read_bytes()
    if b"\r\n" in data:  # CRLF rompe gradlew en Linux
        gradlew.write_bytes(data.replace(b"\r\n", b"\n"))
    make_executable(gradlew)


def main():
    log(f"Raíz del repo: {ROOT}")
    extract_zips()
    relocate()
    place_gradle_files()
    texts = scan_sources()
    if not texts:
        log("ERROR: no se encontró ningún .kt/.java en el repositorio.")
        sys.exit(1)
    ctx = build_context(texts)
    log(f"namespace/applicationId: {ctx['pkg']} | Activity: {ctx['activity']} | "
        f"Kotlin: {ctx['kotlin']} | Compose: {ctx['compose']} | permisos: {ctx['perms']}")
    ensure_settings()
    ensure_properties()
    ensure_root_gradle()
    ensure_app_gradle(ctx)
    ensure_namespace(ctx["pkg"])
    ensure_manifest(ctx)
    fix_manifest()
    ensure_wrapper()
    write_if_missing(ROOT / ".gitignore", GITIGNORE_TPL)
    prune_empty()
    n_res = sum(1 for p in RES.rglob("*") if p.is_file()) if RES.exists() else 0
    log(f"OK: {len(texts)} fuente(s), {n_res} recurso(s). Proyecto listo para ./gradlew assembleDebug")


if __name__ == "__main__":
    main()
