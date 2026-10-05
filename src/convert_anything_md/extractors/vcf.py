from pathlib import Path

from convert_anything_md.extractors.base import (
    ExtractionResult,
    ExtractorError,
    ExtractorUnavailable,
    word_count,
)

"""vCard (.vcf) -> Markdown extractor"""


def _join_qp_soft_breaks(text: str) -> str:
    """Unfold vCard 2.1 QUOTED-PRINTABLE soft line breaks.

    In 2.1 files a QUOTED-PRINTABLE value may end a physical line with ``=``
    and continue on the next line without the leading space that normal
    folding uses. vobject only understands space-folding, so join those lines
    before parsing. Lines on non-QUOTED-PRINTABLE properties are untouched.
    """
    lines = text.splitlines()
    out: list[str] = []
    in_qp = False
    for line in lines:
        if in_qp and out:
            out[-1] = out[-1][:-1] + line
        else:
            out.append(line)
            head = line.split(":", 1)[0].upper()
            if "QUOTED-PRINTABLE" not in head:
                in_qp = False
                continue
        in_qp = out[-1].endswith("=")
    return "\r\n".join(out) + "\r\n"


class VCFExtractor:
    """Extracts Markdown from vCard files (.vcf)."""

    name = "vcf"

    # standard vcard attributes, any additional will not have it's name changed
    vcard_attributes = {
        # Identification Properties
        # FN is given a special place in the md file
        "N": "Name",
        "NICKNAME": "Nickname",
        "PHOTO": "Photo",
        "BDAY": "Birthday",
        "ANNIVERSARY": "Anniversary",
        "GENDER": "Gender",
        "PRONOUNS": "Pronouns",
        "GRAMGENDER": "Grammatical Gender",
        # Contact Details
        "ADR": "Address",
        "LABEL": "Delivery Address",
        "TEL": "Telephone",
        "EMAIL": "Email",
        "IMPP": "Instant Messaging",
        "LANG": "Languages Spoken",
        "AGENT": "Agent/Representative",
        # Geographical Peroperties
        "GEO": "Geolocation",
        "TZ": "Timezone",
        # Organizational Properties
        "TITLE": "Title",
        "ROLE": "Role",
        "MEMBER": "Member",
        "RELATED": "Related",
        "ORG": "Organization",
        "LOGO": "Logo",
        # Informational Properties
        "CATEGORIES": "Categories",
        "NOTE": "Note",
        "SOUND": "Sound",
        "SOCIALPROFILE": "Social Profile",
        "URL": "URL",
        # Calendar Properties and URI/URLs
        "CALADRURI": "Calendar Address",
        "CALURI": "Calendar URI",
        "FBURL": "Calendar Free/Busy URL",
        # Explanatory Properties
        "SORT-STRING": "Sort String",
        "MAILER": "Email Service Provider",
        "NAME": "Source Name",
        "SOURCE": "Directory Source",
        "KIND": "vCard Type",
        "XML": "XML",
        "PRODID": "Product Identifier",
        "REV": "Revision",
        "UID": "Unique Identifier",
        "CLIENTPIDMAP": "Client PID Map",
        "PROFILE": "vCard Profile",
        # Security Properties
        "CLASS": "Privacy Classification",
        "KEY": "Public Encryption Key",
    }

    # Properties that have a defined structure
    STRUCTURED_FIELDS = {
        "N": [
            ("Family", "family"),
            ("Given", "given"),
            ("Additional", "additional"),
            ("Prefix", "prefix"),
            ("Suffix", "suffix"),
        ],
        "ADR": [
            ("PO Box", "box"),
            ("Apt/Suite", "extended"),
            ("Street", "street"),
            ("City", "city"),
            ("Region/State", "region"),
            ("Postal Code", "code"),
            ("Country", "country"),
        ],
    }

    # Nickname is free text with commas, which is different from
    #   Categories and other attributes which vobject returns a list for
    COMMA_SPLIT_FIELDS = {"NICKNAME"}




    def extract(self, path: Path) -> ExtractionResult:
        """Read `path` and return an `ExtractionResult`.

        Raises:
            ExtractorUnavailable: backing tool isn't installed.
            ExtractorError:       tool is installed but extraction failed.
        """
        try:
            import vobject
        except ImportError as exc:
            raise ExtractorUnavailable(
                "The vobject library is required for VCF extraction. "
                "Please install it with 'pip install vobject'."
            ) from exc

        try:
            vcard_data = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError as err:
            raise ExtractorError(f"Failed to read file {path}: {err}") from err

        try:
            components = list(
                vobject.readComponents(_join_qp_soft_breaks(vcard_data))
            )
        except Exception as err:
            raise ExtractorError(f"Failed to parse vCard data: {err}") from err

        if not components:
            raise ExtractorError("Invalid vCard: no vCard found in file.")

        markdown = "\n\n".join(self._render_vcard(c) for c in components)
        wc = word_count(markdown)

        return ExtractionResult(
            markdown=markdown,
            engine=self.name,
            word_count=wc,
        )

    # --- Helper functions ---

    def _render_vcard(self, vcard) -> str:
        """Render a single parsed vCard as Markdown."""
        version = getattr(vcard, "version", None)
        full_name = getattr(vcard, "fn", None)

        if version is None or not version.value:
            raise ExtractorError("Invalid vCard: missing VERSION field.")

        if full_name is None or not full_name.value:
            raise ExtractorError("Invalid vCard: missing FN (Full Name) field.")

        sorted_markdown_lines = {}
        # First we load keys into dictionary to keep sorted structure
        for field_name in self.vcard_attributes:
            if field_name.lower() in vcard.contents:
                sorted_markdown_lines[field_name.upper()] = self.vcard_attributes[
                    field_name.upper()
                ]

        # Next, any attributes not in the dictionary get added to the bottom
        for field_name in vcard.contents:
            if field_name.upper() not in sorted_markdown_lines:
                if field_name.upper() in ["FN", "VERSION"]:
                    continue  # Skip FN and VERSION as they are handled special
                sorted_markdown_lines[field_name.upper()] = field_name.upper()

        markdown_lines = []

        markdown_lines.append(f"## {full_name.value}\n")

        for attr_name, field_name in sorted_markdown_lines.items():
            # _list gets all instances of an item, allowing for multiple
            # emails, addresses, etc.
            items = getattr(vcard, f"{attr_name.lower()}_list", None)
            lines_for_field = self._render_field(attr_name, field_name, items)
            markdown_lines.extend(lines_for_field)

        markdown_lines.append(f"\nvCard Version: {version.value}")
        return "\n".join(markdown_lines)




    def _render_field(self, attr_name, field_name, items):
        """Render a single vCard property in md format"""
        # if there is only 1 of attr_name lines in the file
        if len(items) == 1:
            item = items[0]
            type_param = getattr(item, "type_param", None)
            sub_lines = self._render_value_lines(attr_name, item)

            if (
                attr_name in self.STRUCTURED_FIELDS
                or len(sub_lines) > 1
                or (len(sub_lines) == 1 and type_param)
            ):
                header = [f"- **{field_name}:**"]
                if type_param:
                    header.append(f"  - Type: {type_param}")
                # sub_lines already start with "- " (from _render_value_lines),
                # except for the plain single-value fallback case.
                indented = []
                for ln in sub_lines:
                    indented.append(f"  {ln}" if ln.startswith("- ") else f"  - {ln}")
                return header + indented
            else:
                value_text = sub_lines[0] if sub_lines else ""
                if value_text.startswith("- "):
                    value_text = value_text[2:]
                return [f"- **{field_name}:** {value_text}"]

        # Multiple instances of the same property (e.g. two TEL lines).
        lines = [f"- **{field_name}:**"]
        for item in items:
            type_param = getattr(item, "type_param", None)
            value = getattr(item, "value", None)
            value_text = str(value)

            if type_param:
                lines.append(f"  - {type_param}: {value_text}")
            else:
                lines.append(f"  - {value_text}")
        return lines




    def _render_value_lines(self, attr_name, item):
        """Return a list of text lines representing the value portion
        of a single property instance (no field label, no TYPE).
        For example: Address or names
        """

        value = getattr(item, "value", None)

        if attr_name in self.STRUCTURED_FIELDS:
            lines = []
            for label, sub_attr in self.STRUCTURED_FIELDS[attr_name]:
                sub_val = getattr(value, sub_attr, "")
                if sub_val:
                    lines.append(f"- {label}: {sub_val}")
            return lines if lines else [""]

        if isinstance(value, list):
            # vCard 2.1 nests ORG/CATEGORIES-like values as a list of lists;
            # flatten one level and strip so we render text, not a Python repr.
            flat: list[str] = []
            for v in value:
                if isinstance(v, list):
                    flat.extend(str(x).strip() for x in v)
                else:
                    flat.append(str(v).strip())
            flat = [s for s in flat if s]
            return [f"- {v}" for v in flat] if flat else [""]

        if attr_name in self.COMMA_SPLIT_FIELDS and isinstance(value, str):
            parts = [p.strip() for p in value.split(",") if p.strip()]
            if len(parts) > 1:
                return [f"- {p}" for p in parts]

        return [str(value)]
