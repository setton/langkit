from __future__ import annotations

from dataclasses import dataclass, field
import enum
from functools import lru_cache
import os
import os.path as P
import re
import sys
import traceback
from typing import List, NoReturn, Optional, Union


try:
    import liblktlang as L
except ImportError:
    pass


import langkit.documentation
from langkit.utils import Colors, assert_type, col


class DiagnosticStyle(enum.Enum):
    """Format for diagnostics that Langkit emits: location and text."""

    default = 'default'
    """Human-readable tracebacks."""

    gnu_full = 'gnu-full'
    """Standard GNU format with full paths."""

    gnu_base = 'gnu-base'
    """Standard GNU format with basenames."""


class Diagnostics:
    """
    Holder class that'll store the language definition source dir. Meant to
    be called by manage before functions depending on knowing the language
    source dir can be called.
    """
    has_pending_error = False

    style = DiagnosticStyle.default
    """
    DiagnosticStyle instance to select the diagnostic representation format.

    :type: DiagnosticStyle
    """

    blacklisted_paths = [P.dirname(P.abspath(__file__))]
    """
    List of blacklisted paths. Add to that list to keep paths out of
    diagnostics.
    """

    @classmethod
    def is_langkit_dsl(cls, python_file):
        """
        Return wether `python_file` is langkit DSL.

        :type python_file: str
        :rtype: bool
        """
        # We check that the path of the file is not in the list of blacklisted
        # paths.
        python_file = P.normpath(python_file)
        return all(path not in python_file for path in cls.blacklisted_paths)

    @classmethod
    def set_style(cls, style):
        """
        Set the diagnostic output format.
        :type style: DiagnosticStyle
        """
        cls.style = style


@dataclass(order=True, frozen=True)
class Location:
    """
    Holder for a location in the source code.
    """

    file: str
    """
    Path to the file for this location.
    """

    line: int
    """
    Line number (1-based).
    """

    column: int = field(default=0)
    """
    Column number (1-based). Zero if unspecified.

    :type: int
    """

    end_line: int = field(default=0)
    end_column: int = field(default=0)
    """
    End line and column numbers. Zero if unspecified.
    """

    # RA22-015 TODO: Remove this "zero if unspecified" business when we get rid
    # of the legacy DSL.

    lkt_unit: Optional[L.AnalysisUnit] = field(default=None)

    def gnu_style_repr(self, relative: bool = True) -> str:
        """
        Return a GNU style representation for this Location, in the form::

            file:line:column

        :param relative: When True, the file path will be relative.
        """
        return ":".join([
            P.basename(self.file) if relative else self.file,
            str(self.line),
        ] + ([str(self.column)] if self.column > 0 else []))

    @classmethod
    def from_lkt_node(cls, node: L.LKNode) -> Location:
        """
        Create a Location based on a LKT node.
        """
        return cls(
            node.unit.filename,
            node.sloc_range.start.line,
            node.sloc_range.start.column,
            node.sloc_range.end.line,
            node.sloc_range.end.column,
            node.unit
        )


def extract_library_location(stack=None) -> Optional[Location]:
    """
    Extract the location of the definition of an entity in the language
    specification from a stack trace. Use `traceback.extract_stack()` if no
    stack is provided.
    """
    stack = stack or traceback.extract_stack()

    # Create Location instances for each stack frame
    locs = [Location(file=t[0], line=t[1])
            for t in stack
            if Diagnostics.is_langkit_dsl(t[0]) and "manage.py" not in t[0]]

    return locs[-1] if locs else None


context_stack = []
"""
:type: list[(str, Location, str)]
"""


class Context:
    """
    Add context for diagnostics. For the moment this context is constituted
    of a message and a location.
    """

    def __init__(self, message, location, id=""):
        """
        :param str message: The message to display when displaying the
            diagnostic, to contextualize the location.

        :param Location location: The location associated to the context.

        :param str id: A string that is meant to uniquely identify a category
            of diagnostic. Only one message (the latest) will be shown for each
            category when diagnostics are printed. If id is empty (the default)
            then the context has no category, and it will be considered
            unique, and always be shown.
        """
        self.message = message
        self.location = location
        self.id = id

    def __enter__(self):
        context_stack.append((self.message, self.location, self.id))

    def __exit__(self, exc_type, exc_value, traceback):
        del traceback
        del exc_type
        context_stack.pop()

    def __repr__(self):
        return (
            '<diagnostics.Context message={}, location={}, id={}>'.format(
                self.message, self.location, self.id
            )
        )


class DiagnosticError(Exception):
    pass


class Severity(enum.IntEnum):
    """
    Severity of a diagnostic. For the moment we have two levels, warning and
    error. A warning won't end the compilation process, and error will.
    """
    warning = 1
    error = 2
    non_blocking_error = 3


SEVERITY_COLORS = {
    Severity.warning:            Colors.YELLOW,
    Severity.error:              Colors.RED,
    Severity.non_blocking_error: Colors.RED,
}


def format_severity(severity):
    """
    :param Severity severity:
    """
    msg = ('Error'
           if severity == Severity.non_blocking_error else
           severity.name.capitalize())
    return col(msg, Colors.BOLD + SEVERITY_COLORS[severity])


def get_structured_context():
    """
    From the context global structures, return a structured context locations
    list.

    :rtype: list[(str, Location)]
    """
    c = context_stack
    ids = set()
    locs = set()
    msgs = []

    # We'll iterate once on diagnostic contexts, to:
    # 1. Remove those with null locations.
    # 2. Only keep one per registered id.
    # 3. Only keep one per unique (msg, location) pair.
    for msg, loc, id in reversed(c):
        if loc and (not id or id not in ids) and ((msg, loc) not in locs):
            msgs.append((msg, loc))
            ids.add(id)
            locs.add((msg, loc))

    return msgs


def get_filename(f):
    return (os.path.abspath(f)
            if Diagnostics.style == DiagnosticStyle.gnu_full else
            os.path.basename(f))


def get_current_location() -> Optional[Location]:
    ctx = get_structured_context()
    return ctx[0][1] if ctx else None


def get_parsable_location():
    """
    Returns an error location in the common tool parsable format::

        {file}:{line}:{column}

    Depending on the diagnostic style enabled, `file` will be a base name or a
    full path. Note that this should not be run when `DiagnosticStyle.default`
    is enabled.

    :rtype: str
    """
    assert Diagnostics.style != DiagnosticStyle.default
    ctx = get_structured_context()
    if ctx:
        loc = ctx[0][1]
        path = (P.abspath(loc.file)
                if Diagnostics.style == DiagnosticStyle.gnu_full else
                P.basename(loc.file))
        return "{}:{}:1".format(path, loc.line)
    else:
        return ""


def error(message: str) -> NoReturn:
    """
    Shortcut around ``check_source_language``, for fatal errors.
    """
    check_source_language(False, message)
    # NOTE: The following raise is useless, but is there because mypy is not
    # clever enough to know  that the previous call will never return.
    raise AssertionError("should not happen")


def check_source_language(predicate, message, severity=Severity.error,
                          do_raise=True, ok_for_codegen=False):
    """
    Check predicates related to the user's input in the input language
    definition. Show error messages and eventually terminate if those error
    messages are critical.

    :param bool predicate: The predicate to check.
    :param str message: The base message to display if predicate happens to
        be false.
    :param Severity severity: The severity of the diagnostic.
    :param bool do_raise: If True, raise a DiagnosticError if predicate happens
        to be false.
    :param bool ok_for_codegen: If True, allow checks to be performed during
        code generation. This is False by default as it should be an
        exceptional situation: we want, when possible, most checks to be
        performed before we attempt to emit the generated library (for
        --check-only).
    """
    from langkit.compile_context import get_context

    if not ok_for_codegen:
        ctx = get_context(or_none=True)
        assert ctx is None or ctx.emitter is None

    severity = assert_type(severity, Severity)
    indent = ' ' * 4

    if not predicate:
        message_lines = message.splitlines()
        message = '\n'.join(
            message_lines[:1] + [indent + line for line in message_lines[1:]]
        )

        if Diagnostics.style != DiagnosticStyle.default:
            print('{}: {}'.format(get_parsable_location(), message))
        else:
            print_error(message, get_current_location())

        if severity == Severity.error and do_raise:
            raise DiagnosticError()
        elif severity == Severity.non_blocking_error:
            Diagnostics.has_pending_error = True


class WarningDescriptor:
    """
    Embed information about a class of warnings. Allows to log warning messages
    via the `warn_if` method.
    """

    def __init__(self, name, enabled_by_default, description):
        self.name = name
        self.description = description
        self.enabled_by_default = enabled_by_default

    @property
    def enabled(self):
        """
        Return whether this warning is enabled in the current context.

        :rtype: bool
        """
        from langkit.compile_context import get_context
        return self in get_context().warnings

    def __repr__(self):
        return '<WarningDescriptor {}>'.format(self.name)

    def warn_if(self, predicate, message):
        """
        Helper around check_source_language, to raise warnings, depending on
        whether self is enabled or not in the current context.

        :param bool predicate: The predicate to check.
        :param str message: The base message to display if predicate happens to
            be false.
        """
        check_source_language(not self.enabled or not predicate, message,
                              severity=Severity.warning)


class WarningSet:
    """
    Set of enabled warnings.
    """

    prop_only_entities = WarningDescriptor(
        'prop-only-entities', True,
        'Warn about properties that return AST nodes.'
    )
    unused_bindings = WarningDescriptor(
        'unused-bindings', True,
        'Warn about bindings (in properties) that are unused, or the ones used'
        ' while they are declared as unused.'
    )
    unparser_bad_grammar = WarningDescriptor(
        'unparser-bad-grammar', False,
        'Warn if the grammar is not amenable to the automatic generation of an'
        ' unparser.'
    )
    unused_node_type = WarningDescriptor(
        'unused-node-type', True,
        'Warn if a node type is not used in the grammar, and is not marked as'
        ' abstract nor synthetic.'
    )
    undocumented_public_properties = WarningDescriptor(
        'undocumented-public-properties', True,
        'Warn if a public property is left undocumented.'
    )
    undocumented_nodes = WarningDescriptor(
        'undocumented-nodes', True,
        'Warn if a node is left undocumented.'
    )
    imprecise_field_type_annotations = WarningDescriptor(
        'imprecise-field-type-annotations', True,
        'Warn about parsing field type annotations that are not as precise as'
        ' they could be.'
    )
    available_warnings = [
        prop_only_entities, unused_bindings, unparser_bad_grammar,
        unused_node_type, undocumented_public_properties, undocumented_nodes,
        imprecise_field_type_annotations,
    ]

    def __init__(self):
        self.enabled_warnings = {w for w in self.available_warnings
                                 if w.enabled_by_default}

    def __repr__(self):
        return '<WarningSet [{}]>'.format(', '.join(
            w.name for w in self.enabled_warnings
        ))

    def enable(self, warning):
        """
        Enable the given warning in this WarningSet instance.

        :type warning: WarningDescriptor|str
        """
        if isinstance(warning, str):
            warning = self.lookup(warning)
        self.enabled_warnings.add(warning)

    def disable(self, warning):
        """
        Disable the given warning in this WarningSet instance.

        :type warning: WarningDescriptor|str
        """
        if isinstance(warning, str):
            warning = self.lookup(warning)
        self.enabled_warnings.discard(warning)

    def clone(self):
        """
        Return a copy of this WarningSet instance.

        :rtype: WarningSet
        """
        other = WarningSet()
        other.enabled_warnings = set(self.enabled_warnings)
        return other

    def with_enabled(self, warning):
        """
        Return a copy of this WarningSet instance where `warning` is enabled.

        :type warning: WarningDescriptor|str
        :rtype WarningSet
        """
        other = self.clone()
        other.enable(warning)
        return other

    def with_disabled(self, warning):
        """
        Return a copy of this WarningSet instance where `warning` is disabled.

        :type warning: WarningDescriptor|str
        :rtype WarningSet
        """
        other = self.clone()
        other.disable(warning)
        return other

    def __contains__(self, warning):
        """
        Return whether `warning` is enabled:

        :type: WarningDescriptor
        :rtype: bool
        """
        return warning in self.enabled_warnings

    def lookup(self, name):
        """
        Look for the WarningDescriptor whose name is `name`. Raise a ValueError
        if none matches.

        :type name: str
        :rtype warning: WarningDescriptor
        """
        for w in self.available_warnings:
            if w.name == name:
                return w
        else:
            raise ValueError('Invalid warning: {}'.format(name))

    @classmethod
    def print_list(cls, out=sys.stdout, width=None):
        """
        Display the list of available warnings in `f`.

        :param file out: File in which the list is displayed.
        :param None|int width: Width of the message. If None, use
            os.environ['COLUMNS'].
        """
        if width is None:
            try:
                width = int(os.environ['COLUMNS'])
            except (KeyError, ValueError):
                width = 80
        print('List of available warnings:', file=out)
        for w in cls.available_warnings:
            print('', file=out)
            print('* {}:'.format(w.name), file=out)
            if w.enabled_by_default:
                print('  [enabled by default]', file=out)
            print(langkit.documentation.format_text(w.description, 2, width),
                  file=out)


def check_multiple(predicates_and_messages, severity=Severity.error):
    """
    Helper around check_source_language, check multiple predicates at once.

    :param list[(bool, str)] predicates_and_messages: List of diagnostic
        tuples.
    :param Severity severity: The severity of the diagnostics.
    """
    for predicate, message in predicates_and_messages:
        check_source_language(predicate, message, severity)


def check_type(obj, typ, message=None):
    """
    Like utils.assert_type, but produces a client error instead.

    :param Any obj: The object to check.
    :param T typ: The expected type of obj.
    :param str|None message: The base message to display if type check fails.

    :rtype: T
    """
    try:
        return assert_type(obj, typ)
    except AssertionError as e:
        message = "{}\n{}".format(e.args[0], message) if message else e.args[0]
        check_source_language(False, message)


def errors_checkpoint():
    """
    If there was a non-blocking error, exit the compilation process.
    """
    if Diagnostics.has_pending_error:
        Diagnostics.has_pending_error = False
        raise DiagnosticError()


@lru_cache()
def splitted_text(unit: L.AnalysisUnit) -> List[str]:
    """
    Memoized function to get the splitted text of an unit. Used to not have to
    compute this every time.
    """
    return unit.text.splitlines()


def style_diagnostic_message(string: str) -> str:
    """
    Given a diagnostic message containing possible variable references
    surrounded by backticks, style those references.
    """
    return re.sub("`.*?`", lambda m: col(m.group(), Colors.BOLD), string)


def source_listing(highlight_sloc: Location, lines_after: int = 0) -> str:
    """
    Create a source listing for an error message, centered around a specific
    sloc, that will be highlighted/careted, as in the following example::

        65 | fun test(): Int = b_inst.fun_call
           |                   ^^^^^^^^^^^^^^^

    :param highlight_sloc: The source location that will allow us
        to create the specific listing.
    :param lines_after: The number of lines to print after the given sloc.
    """

    source_buffer = splitted_text(highlight_sloc.lkt_unit)

    ret = []

    line_nb = highlight_sloc.line - 1
    start_offset = highlight_sloc.column - 1
    end_offset = highlight_sloc.end_column - 1

    # Compute the width of the column needed to print line numbers
    line_nb_width = len(str(highlight_sloc.line + lines_after))

    # Precompute the format string for the listing left column
    prefix_fmt = "{{: >{}}} | ".format(line_nb_width)

    def append_line(line_nb, line):
        """
        Append a line to the source listing, given a line number and a line.
        """
        ret.append(col(prefix_fmt.format(line_nb, line),
                       Colors.BLUE + Colors.BOLD))
        ret.append(line)
        ret.append("\n")

    # Append the line containing the sloc
    append_line(line_nb, source_buffer[line_nb])

    # Append the line caretting the sloc in the line above
    caret_line = "".join("^" if start_offset <= i < end_offset else " "
                         for i in range(len(source_buffer[line_nb])))
    append_line("", col(caret_line, Colors.RED + Colors.BOLD))

    # Append following lines up to ``lines_after`` lines
    for line_nb, line in enumerate(
        source_buffer[line_nb + 1:
                      min(line_nb + lines_after + 1, len(source_buffer))],
        line_nb + 1
    ):
        append_line(line_nb, line)

    return "".join(ret)


def print_error(message: str, location: Union[Location, L.LKNode, None]):
    """
    Prints an error.
    """
    error_marker = col(col("error: ", Colors.RED), Colors.BOLD)

    if location is None:
        print(error_marker + message)
        return

    if isinstance(location, L.LKNode):
        location = Location.from_lkt_node(location)

    # Print the basic error (with colors if in tty)
    print(
        "{}: {}{}".format(
            col(location.gnu_style_repr(), Colors.BOLD),
            error_marker,
            style_diagnostic_message(message),
        ),
    )

    # Print the source listing
    if location.lkt_unit is not None:
        print(source_listing(location))


def print_error_from_sem_result(sem_result: L.SemanticResult):
    """
    Prints an error from an lkt semantic result.
    """
    print_error(sem_result.error_message,
                Location.from_lkt_node(sem_result.node))
