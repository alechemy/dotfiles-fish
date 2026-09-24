#!/usr/bin/env python3
"""Project default tool/skill description sizes from reviewed source, without executing it."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import textwrap

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = {"@upstash/context7-pi": "0.1.2", "pi-subagents": "0.65.0", "pi-web-access": "0.27.0",
            "@gotgenes/pi-anthropic-auth": "3.3.1", "@sting8k/pi-vcc": "0.8.0"}
SUBAGENTS_SOURCE = "./local/copilot-delegation/node_modules/pi-subagents"
SOURCE_SHA256 = {
    "@upstash/context7-pi/package.json": "367f6565087be5e89315d3cb171d9f391017124b598b744662c3500705adcb11",
    "@upstash/context7-pi/extensions/context7.ts": "b8b3ae539981a1469678e19771c061e692838e8c1fde6a5a3990962a8671c8e7",
    "@upstash/context7-pi/lib/prompts.ts": "8169441933875f0e6ffd4fb01959141f1c33921f7dea7fd6ab5a8151318e1156",
    "@upstash/context7-pi/lib/tools/resolve-library-id.ts": "d70bcc0abdf8cd3a85be4c79e548089922f5475bfb46978a9cd2ca836e1d5d88",
    "@upstash/context7-pi/lib/tools/query-docs.ts": "d05ace29b2267a18c396f297f07c06c5a7de68a39c034034fc03c86f013c83f3",
    "pi-subagents/package.json": "6cf6be693b51463b000a16c5bbdced421de47ef7e5be406dbd018743341492b5",
    "pi-subagents/index.ts": "a2f11dbe8e200bd8c590441316a9c6ab222318d2aa738670dedcf0e72592dde3",
    "pi-subagents/src/extension/index.ts": "7611db8d751563abe1d325bad8134829e673df52b320d1f061b0b4041c180211",
    "pi-subagents/src/extension/config.ts": "a7954adf4bc11569e8da1269139a31c0958ff79f44450664170e9594fc6e1973",
    "pi-subagents/src/extension/tool-description.ts": "ed78d45483f85b3fae333277175f5c434c037d937e831f35440f4df746029d52",
    "pi-subagents/src/runs/background/wait-tool.ts": "5ed1670e1c560834ca1c2a857440aa6b8152958236a183f86909b36b3d3a1e4f",
    "pi-subagents/src/runs/background/wait-config.ts": "51a0faf216a858a6beda0c3f41cb1b48cc3975ac7d8514758fb7ec2397c42c66",
    "pi-subagents/src/intercom/native-supervisor-channel.ts": "a679c78fdb6048849a35626ce68fb75ac3ab801b60844f16ff4c68d6302f4c55",
    "pi-web-access/package.json": "820c77279eaa539e187191fa01deb931250be14a588667c31b96904f04b9bcb1",
    "pi-web-access/index.ts": "a08fda14e5b37d1b18a2260e36dc7b17f154ace0db4bcaf7ca8a065bcec908cd",
    "@gotgenes/pi-anthropic-auth/package.json": "5679a589745f42641a5b4fdae08193d5440e467ce0e5ee690b2fd104b5c51a93",
    "@gotgenes/pi-anthropic-auth/src/anthropic-message.ts": "6bb7a7763b21686010648ec3788a3435c5d893ecf3196b81a9987589d2ba32e1",
    "@gotgenes/pi-anthropic-auth/src/billing-header.ts": "69c88b5f1eda91a1e070bdc91f07c10c9a607d0ddfabbea4f007f38bb011de55",
    "@gotgenes/pi-anthropic-auth/src/billing-version-sync.ts": "f7c20f42eb244bac1300fec8fb56133fd10c621c09c5db93fc28ad77f5083f47",
    "@gotgenes/pi-anthropic-auth/src/claude-code-version.ts": "ba7d476d487f918c45197bc7e44ccaf8ad2c9efdedd00ad75657fba6fe104a13",
    "@gotgenes/pi-anthropic-auth/src/constants.ts": "03ac3d5c0d54a3e78ba37fe517da58e3e4632950e3d1d331a80830dd6d50df65",
    "@gotgenes/pi-anthropic-auth/src/debug.ts": "a06644b3d52c8040b61338820881ab83acda3a0238b177261b9f5d6c4464991b",
    "@gotgenes/pi-anthropic-auth/src/diagnostics.ts": "edab84a81b0b100aa5a803f1d08ad431c77d0e4ed467e2ea2d51474c6730033c",
    "@gotgenes/pi-anthropic-auth/src/extension-config.ts": "886446388fb75c0094c78caa4196a58025fe2a72ddab762416792d292723751d",
    "@gotgenes/pi-anthropic-auth/src/extra-provider-shaping.ts": "ec95df3f3dd34a2a50cc8d790e35f8d774b506634fdb34893db7f654051add87",  # betterleaks:allow
    "@gotgenes/pi-anthropic-auth/src/host-transport.ts": "6d1634a06cc2cf7be1d53774a9cc2611739cda52b296d944b5abd51d5ecfe517",
    "@gotgenes/pi-anthropic-auth/src/index.ts": "c4d6a98edeac762a8cbf63696aab654e01ca58ed0e7b63dcbd097c6c5d916aaa",
    "@gotgenes/pi-anthropic-auth/src/oauth-transport.ts": "42d1e837a9e30bf5125743ae36e3a788e22dfb483a552249664f767a17411fbb",  # betterleaks:allow
    "@gotgenes/pi-anthropic-auth/src/request-shaping.ts": "efed50553ff7d3cf0f2edc9d77fe9a4404414e1e69fc6408788ccf102046d028",  # betterleaks:allow
    "@gotgenes/pi-anthropic-auth/src/system-prompt-sections.ts": "9d922f8d9feb7a4b6ef45e12b9b47db04052cdf8a096d4f7dac87d2f30aa9036",
    "@gotgenes/pi-anthropic-auth/src/system-prompt-shaping.ts": "524d6e877eec2d58b9d0ba9539b89053f7a65bf34f55d33b11611909e517a138",  # betterleaks:allow
    "@gotgenes/pi-anthropic-auth/src/version-rejection.ts": "aac221a3877251fe90d0afba6d9cba65eedd52d360d44e15a2bbe449b544eef1",
    "@sting8k/pi-vcc/package.json": "d89601e602948374221cf7718125116c3bb79df9a7babb7d78909a3d4b03c9f0",
    "@sting8k/pi-vcc/index.ts": "be1cccbc0ce25b39b5a3649b64b035600b57016c08d0f57424489b72577fb42f",
    "@sting8k/pi-vcc/src/core/settings.ts": "89066c4ceec53e2f8c498d98c2d788a1f5d266873b65f0945dc90c1422776700",
    "@sting8k/pi-vcc/src/tools/recall.ts": "0fce5dea65fb6390a0666521e57974ead7d0ad8ba652dd6962ffeea44dc50ef7",
    "@sting8k/pi-vcc/src/hooks/before-compact.ts": "5d97b089e04ccf9e081775a5216aa2ffb3b9b3630eaba84cc45b98de2528142a",
    "@sting8k/pi-vcc/src/commands/pi-vcc.ts": "132041aa8e73a14d56a360bfd50ae7413e46f875d768a233594ce1a4e1fecf36",
    "@sting8k/pi-vcc/src/commands/vcc-recall.ts": "a0b4933965a394f2ba05f4f723f6ce736e20dd2b89c7383fa269cb7000a78245"
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def source_file(root, relative):
    parts = Path(relative).parts
    require(parts and not Path(relative).is_absolute() and ".." not in parts, "Unsafe source path")
    path = root
    for part in parts:
        path = path / part
        require(not path.is_symlink(), f"Symlinked source: {relative}")
    require(path.is_file(), f"Missing regular source: {relative}")
    return path


def literal_after(text, marker, substitutions=None):
    require(text.count(marker) == 1, f"Source marker changed: {marker}")
    rest = text.split(marker, 1)[1].lstrip()
    require(rest and rest[0] in '\"\'`', f"Expected string literal: {marker}")
    quote, index, output = rest[0], 1, []
    escapes = {"n": "\n", "r": "\r", "t": "\t", "\\": "\\", '"': '"', "'": "'", "`": "`"}
    while index < len(rest):
        char = rest[index]
        if char == quote:
            require(rest[index + 1:].lstrip().startswith((",", ";")), "Unsupported string continuation")
            return "".join(output)
        if char == "\\":
            index += 1
            require(index < len(rest) and rest[index] in escapes, "Unsupported string escape")
            output.append(escapes[rest[index]])
        elif quote == "`" and rest[index:index + 2] == "${":
            end = rest.find("}", index + 2)
            expression = rest[index + 2:end]
            require(end != -1 and expression in (substitutions or {}), "Unsupported template expression")
            output.append(substitutions[expression])
            index = end
        else:
            require(quote == "`" or char not in "\r\n", "Multiline quoted string")
            output.append(char)
        index += 1
    raise ValueError("Unterminated string literal")


def concatenated_description(text):
    require(text.count("description:") == 1 and text.count("promptSnippet:") == 1,
            "Concatenated description markers changed")
    expression = text.split("description:", 1)[1].split("promptSnippet:", 1)[0].strip()
    string = r'"(?:[^"\\\r\n]|\\.)*"'
    require(re.fullmatch(rf'{string}(?:\s*\+\s*{string})*\s*,', expression),
            "Unsupported concatenated description")
    return "".join(json.loads(match.group()) for match in re.finditer(string, expression))


def skill_field(frontmatter, key):
    matches = list(re.finditer(rf"^{re.escape(key)}: *(.*)$", frontmatter, re.MULTILINE))
    require(len(matches) <= 1, f"Duplicate skill field: {key}")
    if not matches:
        return None
    match = matches[0]
    value = match.group(1).rstrip()
    following = frontmatter[match.end():].lstrip("\n").splitlines()
    block = []
    for line in following:
        if line and not line.startswith(" "):
            break
        block.append(line)
    if value in ("|", ">-"):
        body = textwrap.dedent("\n".join(block)).rstrip("\n")
        require(body and not any(line.startswith(" ") for line in body.splitlines()), "Unsupported block indentation")
        if value == "|":
            return body + "\n"
        require("\n\n\n" not in body, "Unsupported folded blank lines")
        return "\n".join(paragraph.replace("\n", " ") for paragraph in body.split("\n\n"))
    require(not any(line.strip() for line in block), "Unsupported scalar continuation")
    require(value and value[0] not in "'\"!&*[{>|" and " #" not in value and ": " not in value,
            f"Unsupported skill scalar: {key}")
    if key in ("name", "description"):
        require(value.lower() not in ("true", "false", "null", "~")
                and not re.fullmatch(
                    r"[-+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][-+]?[0-9]+)?"
                    r"|0o[0-7]+|0x[0-9a-fA-F]+|[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN)", value),
                f"Non-string skill {key}")
    return value


def skill_description(text):
    require(text.startswith("---\n") and "\n---\n" in text[4:], "Missing skill frontmatter")
    frontmatter = text[4:].split("\n---\n", 1)[0]
    name = skill_field(frontmatter, "name")
    description = skill_field(frontmatter, "description")
    manual = skill_field(frontmatter, "disable-model-invocation")
    require(name and re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name), "Unsupported skill name")
    require(description and description.strip(), "Missing skill description")
    require(manual in (None, "true", "false"), "Unsupported manual skill flag")
    return name, description, manual == "true"


def skill_files(root, include_root_files):
    require(root.is_dir() and not root.is_symlink(), "Missing or symlinked skill directory")
    entries = sorted(root.iterdir())
    require(not any(p.name in (".gitignore", ".ignore", ".fdignore") for p in entries),
            "Skill ignore rules require review")
    skill = root / "SKILL.md"
    if skill.exists() or skill.is_symlink():
        yield source_file(root, "SKILL.md")
        return
    for path in entries:
        if path.name.startswith(".") or path.name == "node_modules":
            continue
        require(not path.is_symlink(), "Symlinked skill discovery requires review")
        if path.is_dir():
            yield from skill_files(path, False)
        elif include_root_files and path.suffix == ".md":
            yield source_file(root, path.name)


def sizes(text):
    return {"characters": len(text), "utf8_bytes": len(text.encode("utf-8"))}


def measure(repo, packages_root, subagents_root):
    fragment_data = source_file(repo, "stow/pi/.pi/agent/settings.fragment.json").read_bytes()
    fragment = json.loads(fragment_data)
    declared = PACKAGES
    expected = [SUBAGENTS_SOURCE if name == "pi-subagents" else f"npm:{name}@{version}"
                for name, version in declared.items()]
    package_roots = {name: subagents_root if name == "pi-subagents" else packages_root / name
                     for name in declared}
    require(fragment.get("packages") == expected, "Declared package set/filters changed; review the projection")
    inputs = {"tracked/settings.fragment.json": hashlib.sha256(fragment_data).hexdigest()}
    sources, manifests = {}, {}

    def read(root, relative, label):
        data = source_file(root, relative).read_bytes()
        inputs[label] = hashlib.sha256(data).hexdigest()
        return data.decode("utf-8")

    for name, version in declared.items():
        manifest = json.loads(read(package_roots[name], "package.json", f"{name}/package.json"))
        require(manifest.get("name") == name and manifest.get("version") == version, f"Installed version mismatch: {name}")
        manifests[name] = manifest
    for relative, digest in SOURCE_SHA256.items():
        name = next(name for name in declared if relative.startswith(name + "/"))
        sources[relative] = read(package_roots[name], relative[len(name) + 1:], relative)
        require(inputs[relative] == digest, f"Reviewed source drift: {relative}")

    tools = []

    def tool(package, name, text):
        tools.append({"package": package, "name": name, **sizes(text)})

    context7 = sources["@upstash/context7-pi/lib/prompts.ts"]
    for name, constant in (("resolve-library-id", "RESOLVE_LIBRARY_ID_DESCRIPTION"), ("query-docs", "QUERY_DOCS_DESCRIPTION")):
        tool("@upstash/context7-pi", name, literal_after(context7, f"export const {constant} ="))

    subagents = sources["pi-subagents/src/extension/tool-description.ts"]
    constants = {}
    for constant in ("WORKFLOW_RESOURCE_GUIDANCE", "AGENT_SELECTION_GUIDANCE", "WORKFLOW_SCRIPT_PORTABILITY_GUIDANCE",
                     "WORKFLOW_LANES_GUIDANCE", "WORKFLOW_HOST_GUIDANCE", "EXTERNAL_CLI_RUNNER_GUIDANCE"):
        constants[constant] = literal_after(subagents, f"const {constant} =")
    tool("pi-subagents", "subagent", literal_after(subagents, "export const DEFAULT_SUBAGENT_TOOL_DESCRIPTION =", constants))
    wait = sources["pi-subagents/src/runs/background/wait-tool.ts"]
    wait_disabled = 'enabled ? "" : "\\n\\nConfigured behavior: bg_wait is disabled by config.waitTool or PI_SUBAGENT_WAIT_TOOL_ENABLED and returns immediately without blocking."'
    tool("pi-subagents", "bg_wait", literal_after(wait, "const description =", {wait_disabled: ""}))
    supervisor = sources["pi-subagents/src/intercom/native-supervisor-channel.ts"]
    supervisor_name = literal_after(supervisor, "export const NATIVE_SUPERVISOR_TOOL_NAME =")
    parent_marker = "function buildParentSupervisorTool("
    require(supervisor.count(parent_marker) == 1, "Parent supervisor registration changed")
    parent_definition = supervisor.split(parent_marker, 1)[1].split("parameters:", 1)[0]
    tool("pi-subagents", supervisor_name, literal_after(parent_definition, "description:"))

    web = sources["pi-web-access/index.ts"]
    substitutions = {
        "fetchContentStorageNote": "Full original content is stored for retrieval with get_search_content.",
        "storedContentSources": "web_search, source_check, or fetch_content",
    }
    for key, name in (("webSearch", "web_search"), ("sourceCheck", "source_check"),
                      ("fetchContent", "fetch_content"), ("getSearchContent", "get_search_content")):
        marker = f"name: toolNames.{key},"
        require(web.count(marker) == 1, "Web tool registration changed")
        definition = web.split(marker, 1)[1].split("parameters:", 1)[0]
        tool("pi-web-access", name, literal_after(definition, "description:", substitutions))

    vcc = sources["@sting8k/pi-vcc/src/tools/recall.ts"].split("parameters:", 1)[0]
    tool("@sting8k/pi-vcc", "vcc_recall", concatenated_description(vcc))

    skills, manual, names = [], [], set()
    roots = [("shared", repo / "stow/agents/.agents/skills", False)]
    for name, manifest in manifests.items():
        require(isinstance(manifest.get("pi"), dict), "Package manifest discovery changed")
        paths = manifest["pi"].get("skills", [])
        require(paths in ([], ["./skills"]), "Package skill manifest/filters require review")
        roots.extend((name, package_roots[name] / "skills", True) for _ in paths)
    for origin, root, include_root_files in roots:
        for path in skill_files(root, include_root_files):
            relative = path.relative_to(root).as_posix()
            text = read(root, relative, f"{origin}/skills/{relative}")
            name, description, is_manual = skill_description(text)
            require(name not in names, "Skill name collision requires review")
            names.add(name)
            if is_manual:
                manual.append(name)
            else:
                skills.append({"package": origin, "name": name, **sizes(description)})

    def total(rows):
        return {"count": len(rows), **{key: sum(row[key] for row in rows) for key in ("characters", "utf8_bytes")}}

    fingerprint = "".join(f"{digest}  {name}\n" for name, digest in sorted(inputs.items()))
    return {
        "method": "source-derived default-registration projection; not observed runtime registration",
        "declared_versions": declared,
        "unmeasured_packages": {},
        "tools": tools, "tool_descriptions": total(tools),
        "skills": skills, "advertised_skill_descriptions": total(skills),
        "manual_skills_excluded": sorted(manual),
        "assumptions": ["Parent session after ordinary session_start; no environment or project overrides",
                        "Default Subagents description and enabled bg_wait; all four default Web Access tools",
                        "Declared packages and tracked shared skills only"],
        "exclusions": ["Tool schemas and names/labels, promptSnippet, promptGuidelines",
                       "Skill XML escaping/wrappers, names, locations, instructions and bodies",
                       "Built-in tools, commands, prompts, roles, dynamic resources and runtime tool activation",
                       "User/project config, credentials, hooks, providers, transcripts and wire serialization",
                       "No provider token measurement"],
        "input_sha256": dict(sorted(inputs.items())),
        "input_set_sha256": hashlib.sha256(fingerprint.encode("utf-8")).hexdigest(),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packages-root", type=Path, required=True, help="Installed npm node_modules source directory")
    parser.add_argument("--subagents-root", type=Path, required=True, help="Installed local delegation guard package directory")
    args = parser.parse_args()
    try:
        print(json.dumps(measure(ROOT, args.packages_root, args.subagents_root), indent=2, ensure_ascii=False))
    except (OSError, ValueError) as error:
        print(f"measure-agent-tooling: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
