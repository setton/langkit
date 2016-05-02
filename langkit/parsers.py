"""
This file contains the base logic for the parser generator. It declares a base
class, Parser, from which every parsing primitive derives.

It contains both the public interface to the parsers (eg. the Rows, Opt, Or,
etc.. classes), and the engine implementation that will actually emit the final
code.

The way the code is generated is by recursively visiting the parser structure
and emitting the code corresponding to the declared parser. For example, for
the following rule definition::

    if_stmt=Row("if", G.expression, "then", G.statements, "endif")

The row structure will be visited recursively, emitting the corresponding code.

The parser generator generates separate functions for every rule that is
declared. It means that in the case of the previous example, a rule will be
declared for if_stmt, and for the `expression` and `statements` rule, that are
not defined in the example, but relied on explicitly.
"""

from __future__ import absolute_import

from copy import copy
import inspect
from itertools import chain

from langkit import compiled_types, names
from langkit.common import gen_name, gen_names
from langkit.compile_context import get_context
from langkit.compiled_types import (
    CompiledType, BoolType, Token, ASTNode, decl_type
)
from langkit.diagnostics import (
    extract_library_location, context, check_source_language
)
from langkit.template_utils import TemplateEnvironment
from langkit.utils import (Colors, common_ancestor, copy_with, col,
                           type_check_instance)


class GeneratedParser(object):
    """Simple holder for generated parsers."""

    def __init__(self, name, spec, body):
        self.name = name
        self.spec = spec
        self.body = body


def render(*args, **kwargs):
    return compiled_types.make_renderer().update({
        'is_tok':      type_check_instance(Tok),
        'is_row':      type_check_instance(Row),
        'is_class':    inspect.isclass,
        'get_context': get_context
    }).render(*args, **kwargs)


class ParserCodeContext(object):
    """
    ParserCodeContext encapsulates the return value of the
    Parser.generate_code primitive. A parser's code generation will return:

        pos_var_name: The name of the variable that points to the new
        position of the parser.

        res_var_name: The name of the variable that points to the result of
        the parser.

        code: The code generated by the parser, that will be encapsulated
        by the parent parser.

        var_defs: A list of tuples of type (string, CompiledType). Each
        tuple represents a variable declaration that must be inserted at
        the top level (usually the function that will encapsulate this
        parser).
    """

    def __init__(self, pos_var_name, res_var_name, code, var_defs):
        self.pos_var_name = pos_var_name
        self.res_var_name = res_var_name
        self.code = code
        self.var_defs = var_defs


def resolve(parser):
    """
    :type parser: Parser|types.Token|ParserContainer
    :rtype: Parser
    """
    if isinstance(parser, Parser):
        return parser
    elif isinstance(parser, Token):
        return Tok(parser)
    elif isinstance(parser, str):
        return Tok(parser)
    else:
        raise Exception("Cannot resolve parser {}".format(parser))


class Grammar(object):
    """
    Holder for parsing rules.

    Parsing rules can be added incrementally while referencing each other: this
    class will automatically resolve forward references when needed.
    """

    def __init__(self, main_rule_name):
        self.rules = {}
        self.main_rule_name = main_rule_name
        self.location = extract_library_location()

    def context(self):
        return context("In definition of grammar", self.location)

    def add_rules(self, **kwargs):
        """
        Add rules to the grammar.  The keyword arguments will provide a name to
        rules.

        :param dict[str, Parser] kwargs: The rules to add to the grammar.
        """
        for name, rule in kwargs.items():
            assert name not in self.rules, (
                "Rule {} is already present in the grammar".format(name)
            )
            self.rules[name] = rule
            rule.set_name(names.Name.from_lower(name))
            rule.set_grammar(self)
            rule.is_root = True

    def __getattr__(self, rule_name):
        """
        Build and return a Defer parser that references the above rule.

        :param str rule_name: The name of the rule.
        """
        return Defer(rule_name, lambda: self.rules[rule_name])

    def get_unreferenced_rules(self):
        """
        Return a set of names for all rules that are not transitively
        referenced by the main rule.

        :rtype: set[str]
        """
        # We'll first build the set of rules that are referenced, then we'll
        # know the ones not referenced.
        referenced_rules = set()

        def visit_parser(parser):
            """
            Visit all subparsers in "parser" and call "visit_rule" for Defer
            parsers.

            :param Parser parser: Parser to visit.
            """
            if isinstance(parser, Defer):
                visit_rule(parser.name)

            for sub_parser in parser.children():
                visit_parser(sub_parser)

        def visit_rule(rule_name):
            """
            Register "rule_name" as referenced and call "visit_parser" on the
            root parser that implements it. Do nothing if "rule_name" is
            already registered to avoid infinite recursion.

            :param str rule_name: Name for the rule to visit.
            """
            if rule_name in referenced_rules:
                return
            referenced_rules.add(rule_name)
            visit_parser(self.rules[rule_name])

        # The following will fill "referenced_rules" thanks to recursion
        visit_rule(self.main_rule_name)

        return set(self.rules) - referenced_rules


class Parser(object):
    """Base class for parsers building blocks."""

    # noinspection PyMissingConstructor
    def __init__(self):
        self.location = None
        self._mod = None
        self.gen_fn_name = gen_name(self.base_name)
        self.grammar = None
        self.is_root = False
        self._name = names.Name("")

    @property
    def base_name(self):
        """
        Return a simple name (names.Name instance) for this parser.

        The result is used as a base name for the generated function name.
        """
        return names.Name.from_camel(type(self).__name__ + "Parse")

    @property
    def name(self):
        return self._name.lower

    def discard(self):
        return False

    def __or__(self, other):
        """Return a new parser that matches this one or `other`."""

        # Optimization: if we are building an `Or` parser out of other `Or`
        # parsers, flatten the result.

        # Here, we used to mutate existing parsers instead of cloning them.
        # This is bad since parsers can be shared, and user expect such
        # combinatory operations to create new parsers without affecting
        # existing ones.

        alternatives = []
        other_parser = resolve(other)

        if isinstance(self, Or):
            alternatives.extend(self.parsers)
        else:
            alternatives.append(self)

        if isinstance(other_parser, Or):
            alternatives.extend(other_parser.parsers)
        else:
            alternatives.append(other_parser)

        return Or(*alternatives)

    def __xor__(self, transform_fn):
        """
        :type transform_fn: (T) => U
        :rtype: Transform
        """
        return Transform(self, transform_fn)

    def set_location(self, location):
        """
        Set the source location where this parser is defined. This is useful
        for error reporting purposes.

        :type location: langkit.diagnostics.Location
        """
        self.location = location
        for c in self.children():
            c.set_location(self.location)

    def error_context(self):
        """
        Helper that will return a error context manager with parameters set
        for the grammar definition.

        :return:
        """
        return context("In definition of grammar rule {}".format(self.name),
                       self.location)

    def set_grammar(self, grammar):
        """
        Associate `grammar` to this parser and to all its children.

        :param Grammar grammar: The grammar instance.
        """
        for c in self.children():
            c.set_grammar(grammar)
        self.grammar = grammar

    def set_name(self, name):
        """
        Rename this parser and all its children so that `name` is part of the
        corresponding function in the generated code.

        :param names.Name name: The name to include in the name of this parser
            tree.
        """
        for c in self.children():
            if not c._name and not isinstance(c, Defer):
                c.set_name(name)

        self._name = name
        self.gen_fn_name = gen_name(name + self.base_name)

    def is_left_recursive(self):
        """Return whether this parser is left-recursive."""
        return self._is_left_recursive(self.name)

    def _is_left_recursive(self, rule_name):
        """
        Private function used only by is_left_recursive, will explore the
        parser tree to verify whether the named parser with name rule_name is
        left recursive or not.
        """
        raise NotImplementedError()

    # noinspection PyMethodMayBeStatic
    def children(self):
        """
        Parsers are combined to create new and more complex parsers.  They make
        up a parser tree.  Return a list of children for this parser.

        Subclasses should override this method if they have children.
        """
        return []

    def compute_fields_types(self):
        """
        Infer ASTNode's fields from this parsers tree.

        This method recurses over child parsers.  Parser subclasses must
        override this method if they contribute to fields typing.
        """
        for child in self.children():
            child.compute_fields_types()

    def compile(self):
        """
        Emit code for this parser as a function into the global context.
        """
        t_env = TemplateEnvironment()
        t_env._self = self

        # Don't emit code twice for the same parser
        if self.gen_fn_name in get_context().fns:
            return
        get_context().fns.add(self.gen_fn_name)

        t_env.parser_context = (
            self.generate_code()
        )

        get_context().generated_parsers.append(GeneratedParser(
            self.gen_fn_name,
            render('parsers/fn_profile_ada', t_env),
            render('parsers/fn_code_ada', t_env)))

    def get_type(self):
        """
        Return a descriptor for the type this parser returns in the generated
        code.  It can be either the Token class or a CompiledType subtype.

        Subclasses must override this method.
        """
        raise NotImplementedError()

    def gen_code_or_fncall(self, pos_name="pos"):
        """
        Return generated code for this parser into the global context.

        `pos_name` is the name of a variable that contains the position of the
        next token in the lexer.

        Either the "parsing code" is returned, either it is emitted in a
        dedicated function and a call to it is returned instead.  This method
        relies on the subclasses-defined `generated_code` for "parsing code"
        generation.

        :param str|names.Name pos_name: The name of the position variable.
        :rtype: ParserCodeContext
        """

        if self.name and get_context().verbosity.debug:
            print "Compiling rule : {0}".format(
                col(self.gen_fn_name, Colors.HEADER)
            )

        # Users must be able to run parsers that implement a named rule, so
        # generate dedicated functions for them.
        if self.is_root:

            # The call to compile will add the declaration and the definition
            # (body) of the function to the compile context.
            self.compile()

            # Generate a call to the previously compiled function, and return
            # the context corresponding to this call.
            pos, res = gen_names("fncall_pos", "fncall_res")
            fncall_block = render(
                'parsers/fn_call_ada',
                _self=self, pos_name=pos_name,
                pos=pos, res=res
            )

            return ParserCodeContext(
                pos_var_name=pos,
                res_var_name=res,
                code=fncall_block,
                var_defs=[
                    (pos, Token),
                    (res, self.get_type())
                ]
            )

        else:
            return self.generate_code(pos_name)

    def generate_code(self, pos_name="pos"):
        """
        Return generated code for this parser into the global context.

        Subclasses must override this method.  It is a low-level routine used
        by the `gen_code_or_fncall` method.  See above for arguments meaning.

        :param str pos_name: The name of the position variable, which is the
            position of the current token in the lexer stream.
        """
        raise NotImplementedError()


class Tok(Parser):
    """Parser that matches a specific token."""

    def __repr__(self):
        return "Tok({0})".format(repr(self.val))

    def discard(self):
        return not self.keep

    def _is_left_recursive(self, rule_name):
        return False

    def __init__(self, val, keep=False):
        """
        Create a parser that matches `tok`.
        """
        Parser.__init__(self)
        self.val = val
        ":type: Enum|str"
        self.keep = keep

    def get_type(self):
        return Token

    def generate_code(self, pos_name="pos"):

        # Generate the code to match the token of kind 'token_kind', and return
        # the corresponding context.
        pos, res = gen_names("tk_pos", "tk_res")
        code = render(
            'parsers/tok_code_ada',
            _self=self, pos_name=pos_name,
            pos=pos, res=res,
            token_kind=get_context().lexer.ada_token_name(self.val)
        )

        return ParserCodeContext(
            pos_var_name=pos,
            res_var_name=res,
            code=code,
            var_defs=[(pos, Token), (res, Token)]
        )


class Or(Parser):
    """Parser that matches what the first sub-parser accepts."""

    def _is_left_recursive(self, rule_name):
        return any(parser._is_left_recursive(rule_name)
                   for parser in self.parsers)

    def __repr__(self):
        return "Or({0})".format(", ".join(repr(m) for m in self.parsers))

    def __init__(self, *parsers):
        """
        Create a parser that matches any thing that the first parser in
        `parsers` accepts.

        :type parsers: list[Parser|Token|type]
        """
        Parser.__init__(self)
        self.parsers = [resolve(m) for m in parsers]

        # Typing resolution for this parser is a recursive process.  So first
        # we need to prevent infinite recursions (because of recursive
        # grammars)...
        self.is_processing_type = False

        # ... and we want to memoize the result.
        self.cached_type = None

    def children(self):
        return self.parsers

    def get_type(self):
        if self.cached_type:
            return self.cached_type

        # Callers are already visiting this node, so we cannot return its type
        # right now.  Return None so that it doesn't contribute to type
        # resolution.
        if self.is_processing_type:
            return None

        try:
            self.is_processing_type = True
            types = set()
            for m in self.parsers:
                t = m.get_type()
                if t:
                    types.add(t)

            # There are two possibilities:
            #  - if all alternatives return AST nodes: then this parser's
            #    return type is the common ancestor for all of these.
            #  - otherwise, make sure that all alternatives return exactly the
            #    same type.
            if all(issubclass(t, ASTNode) for t in types):
                res = common_ancestor(*types)
            else:
                typs = list(types)

                assert all(type(t) == type(typs[0]) for t in typs)
                res = typs[0]

            self.cached_type = res
            return res
        finally:
            self.is_processing_type = False

    def generate_code(self, pos_name="pos"):
        pos, res = gen_names('or_pos', 'or_res')
        t_env = TemplateEnvironment(
            _self=self,

            # List of ParserCodeContext instances for the sub-parsers,
            # encapsulating their results.
            results=[
                m.gen_code_or_fncall(pos_name)
                for m in self.parsers
            ],

            # Generate a name for the exit label (when one of the sub-parsers
            # has matched).
            exit_label=gen_name("Exit_Or"),

            pos=pos,
            res=res,

            # Final type of the result of the Or parser
            typ=decl_type(self.get_type())
        )

        code = render('parsers/or_code_ada', t_env)

        return ParserCodeContext(
            pos_var_name=t_env.pos,
            res_var_name=t_env.res,
            code=code,

            # For var defs, we create a new list that is the concatenation of
            # all the sub parsers variable definitions, adding the Or parser's
            # own pos and res variables.
            var_defs=list(chain(
                [(pos, Token), (res, self.get_type())],
                *[sr.var_defs for sr in t_env.results]
            ))
        )


def always_make_progress(parser):
    """
    Return whether `parser` cannot match an empty sequence of tokens.

    :param Parser parser: The parser to evaluate.
    """
    if isinstance(parser, List):
        return not parser.empty_valid or always_make_progress(parser.parser)
    return not isinstance(parser, (Opt, Null))


class Row(Parser):
    """Parser that matches a what sub-parsers match in sequence."""

    def _is_left_recursive(self, rule_name):
        for parser in self.parsers:
            res = parser._is_left_recursive(rule_name)
            if res:
                return True
            if always_make_progress(parser):
                break
        return False

    def __repr__(self):
        return "Row({0})".format(", ".join(repr(m) for m in self.parsers))

    def __init__(self, *parsers):
        """
        Create a parser that matches the sequence of matches for all
        sub-parsers in `parsers`.

        If a parser is none it will be ignored. This allows to create
        programmatic helpers that generate rows more easily.

        :type parsers: list[Parser|types.Token|type]
        """
        Parser.__init__(self)

        self.parsers = [resolve(m) for m in parsers if m]

        # The type this row returns is initialized either when assigning a
        # wrapper parser or when trying to get the type (though the get_type
        # method) while no wrapper has been assigned.
        self.typ = None

        self.components_need_inc_ref = True
        self.args = []
        self.allargs = []

    def assign_wrapper(self, parser):
        """
        Associate `parser` as a wrapper for this Row.

        Note that a Row can have at most only one wrapper, so this does nothing
        if this Row is a root parser.

        :param Parser parser: The parser to associate to this row.
        """
        assert not self.is_root and not self.typ, (
            "Row parsers do not represent a concrete result. They must be used"
            " by a parent parser, such as Extract or Transform."
        )

        self.typ = parser.get_type()

    def children(self):
        return self.parsers

    def get_type(self):
        # A Row parser never yields a concrete result itself
        return None

    def generate_code(self, pos_name="pos"):
        t_env = TemplateEnvironment(pos_name=pos_name)
        t_env._self = self

        t_env.pos, t_env.res = gen_names("row_pos", "row_res")
        decls = [(t_env.pos, Token)]

        t_env.subresults = list(gen_names(*[
            "row_subres_{0}".format(i)
            for i in range(len(self.parsers))
        ]))
        t_env.exit_label = gen_name("row_exit_label")

        self.args = [r for r, m in zip(t_env.subresults, self.parsers)
                     if not m.discard()]
        self.allargs = [r for r, m in zip(t_env.subresults, self.parsers)]

        bodies = []
        for i, (parser, subresult) in enumerate(zip(self.parsers,
                                                    t_env.subresults)):
            t_subenv = TemplateEnvironment(
                t_env, parser=parser, subresult=subresult, i=i,
                parser_context=parser.gen_code_or_fncall(t_env.pos)
            )
            decls += t_subenv.parser_context.var_defs
            if not parser.discard():
                decls.append((subresult, parser.get_type()))

            bodies.append(render('parsers/row_submatch_ada', t_subenv))

        code = render('parsers/row_code_ada', t_env, body='\n'.join(bodies))

        return ParserCodeContext(
            pos_var_name=t_env.pos,
            res_var_name=t_env.res,
            code=code,
            var_defs=decls
        )

    def __getitem__(self, index):
        """
        Return a parser that matches `self` and that discards everything except
        the `index`th field in the row.
        """
        return Extract(self, index)


class List(Parser):
    """Parser that matches a list.  A sub-parser matches list items."""

    def _is_left_recursive(self, rule_name):
        res = self.parser._is_left_recursive(rule_name)
        assert not(
            res and (self.empty_valid or not always_make_progress(self.parser))
        )
        return res

    def __repr__(self):
        return "List({0})".format(
            repr(self.parser) + (", sep={0}".format(self.sep)
                                 if self.sep else "")
        )

    def __init__(self, parser, sep=None, empty_valid=False, revtree=None):
        """
        Create a parser that matches a list of elements.

        Each element will be matched by `parser`.  If `sep` is provided, it is
        a parser that is used to match separators between elements.

        By default, this parser will not match empty sequences but it will if
        `empty_valid` is True.

        If `revtree` is provided, it must be an ASTNode subclass.  It is then
        used to fold the list into a binary tree.

        :type sep: types.Token|string
        :type empty_valid: bool
        """
        Parser.__init__(self)
        self.parser = resolve(parser)
        self.sep = resolve(sep) if sep else None
        self.empty_valid = empty_valid
        self.revtree_class = revtree

        if empty_valid:
            assert not self.revtree_class

    def children(self):
        return [self.parser]

    def get_type(self):
        if self.revtree_class:
            return common_ancestor(self.parser.get_type(), self.revtree_class)
        else:
            return self.parser.get_type().list_type()

    def compute_fields_types(self):
        Parser.compute_fields_types(self)

        # If this parser does no folding, it does not contribute itself to
        # fields typing, so we can stop here.
        if not self.revtree_class:
            return

        assert len(self.revtree_class.get_parse_fields()) == 2, (
            "For folding, revtree classes must have two fields"
        )

        self.revtree_class.set_types([self.get_type(), self.get_type()])

    def generate_code(self, pos_name="pos"):

        self.get_type().add_to_context()
        cpos = gen_name("lst_cpos")
        parser_context = self.parser.gen_code_or_fncall(cpos)
        sep_context = (
            self.sep.gen_code_or_fncall(cpos)
            if self.sep else
            ParserCodeContext(None, None, None, [])
        )

        if self.revtree_class:
            self.revtree_class.add_to_context()

        t_env = TemplateEnvironment(
            pos_name=pos_name,
            _self=self,
            pos=gen_name("lst_pos"),
            res=gen_name("lst_res"),
            cpos=cpos,
            parser_context=parser_context,
            sep_context=sep_context
        )

        decls = [
            (t_env.pos, Token),
            (t_env.res, self.get_type()),
            (t_env.cpos, Token),
        ] + parser_context.var_defs + sep_context.var_defs

        return ParserCodeContext(
            pos_var_name=t_env.pos,
            res_var_name=t_env.res,
            code=render('parsers/list_code_ada', t_env),
            var_defs=decls
        )


class Opt(Parser):
    """
    Parser that matches something if possible or that matches an empty sequence
    otherwise.
    """

    def _is_left_recursive(self, rule_name):
        return self.parser._is_left_recursive(rule_name)

    def __repr__(self):
        return "Opt({0})".format(self.parser)

    def __init__(self, parser, *parsers):
        """
        Create a parser that matches `parser` and then `parsers` if possible or
        matches an empty sequence otherwise.  The result is equivalent to::

            Opt(Row(parser, *parsers)).
        """
        Parser.__init__(self)
        self._booleanize = False
        self._is_error = False
        self.contains_anonymous_row = bool(parsers)
        self.parser = Row(parser, *parsers) if parsers else resolve(parser)

    def error(self):
        """
        Returns the self parser, modified to function as an error recovery
        parser.

        The semantic of Opt in this case is that it will try to parse it's
        sub parser, and when failing, it will add a diagnostic to the
        parser's diagnostic list.

        NOTE: There is no diagnostics backtracking if the parent parser is
        discarded. That means that you should only use this parser in cases
        in which you are sure that you are in a successfull branch of your
        parser. This is neither checked statically nor dynamically so use
        with care!

        :rtype: Opt
        """
        return copy_with(self, _is_error=True)

    def as_bool(self):
        """
        Returns the self parser, modified to return a bool rather than the
        sub-parser result. The result will be true if the parse was
        successful, false otherwise.

        This is typically useful to store specific tokens as attributes,
        for example in Ada, you'll mark a subprogram as overriding with the
        "overriding" keyword, and we want to store that in the tree as a
        boolean attribute, so we'll use::

            Opt("overriding").as_bool()

        :rtype: Opt
        """
        new = copy_with(self, _booleanize=True)
        if new.contains_anonymous_row:
            # What the sub-parser will match will not be returned, so there is
            # no need to generate an anonymous row type.  Tell so to the
            # Row sub-parser.
            assert isinstance(new.parser, Row)
            new.parser.assign_wrapper(new)
        return new

    def children(self):
        return [self.parser]

    def get_type(self):
        return BoolType if self._booleanize else self.parser.get_type()

    def generate_code(self, pos_name="pos"):
        parser_context = copy(
            self.parser.gen_code_or_fncall(pos_name)
        )

        t_env = TemplateEnvironment(
            pos_name=pos_name,
            _self=self,
            bool_res=gen_name("opt_bool_res"),
            parser_context=parser_context
        )

        return copy_with(
            parser_context,
            code=render('parsers/opt_code_ada', t_env),
            res_var_name=(t_env.bool_res if self._booleanize
                          else parser_context.res_var_name),
            var_defs=parser_context.var_defs + ([(t_env.bool_res, BoolType)]
                                                if self._booleanize else [])
        )

    def __getitem__(self, index):
        """Same as Row.__getitem__:
        Return a parser that matches `self` and that discards everything except
        the `index`th field in the row.

        Used as a shortcut, will only work if the Opt's sub-parser is a row.
        """
        m = self.parser
        assert isinstance(m, Row)
        return Opt(Extract(m, index))


class Extract(Parser):
    """
    Wrapper parser used to discard everything from a Row parser except a single
    field in it.
    """

    def _is_left_recursive(self, rule_name):
        return self.parser._is_left_recursive(rule_name)

    def __repr__(self):
        return "{0} >> {1}".format(self.parser, self.index)

    def __init__(self, parser, index):
        """
        :param Row parser: The parser that will serve as target for
            extract operation.
        :param int index: The index you want to extract from the row.
        """
        Parser.__init__(self)
        self.parser = parser
        self.index = index
        assert isinstance(self.parser, Row)
        self.parser.components_need_inc_ref = False

    def children(self):
        return [self.parser]

    def get_type(self):
        return self.parser.parsers[self.index].get_type()

    def generate_code(self, pos_name="pos"):
        self.parser.assign_wrapper(self)

        return copy_with(
            self.parser.gen_code_or_fncall(pos_name),
            res_var_name=self.parser.allargs[self.index]
        )


class Discard(Parser):
    """Wrapper parser used to discard the match."""

    def discard(self):
        return True

    def _is_left_recursive(self, rule_name):
        return self.parser._is_left_recursive(rule_name)

    def __repr__(self):
        return "Discard({0})".format(self.parser)

    def __init__(self, parser):
        Parser.__init__(self)

        parser = resolve(parser)
        if isinstance(parser, Row):
            parser.assign_wrapper(self)

        self.parser = parser

    def children(self):
        return [self.parser]

    def get_type(self):
        # Discard parsers return nothing!
        return None

    def generate_code(self, pos_name="pos"):
        return self.parser.gen_code_or_fncall(pos_name)


class Defer(Parser):
    """Stub parser used to implement forward references."""

    @property
    def parser(self):
        if not self._parser:
            self._parser = self.parser_fn()
        return self._parser

    @property
    def name(self):
        # Don't rely on `self.parser` since it may not be available right now
        # (that's why it is deferred in the first place).
        return self.rule_name

    def _is_left_recursive(self, rule_name):
        return self.name == rule_name

    def __repr__(self):
        return "Defer({0})".format(self.name)

    def __init__(self, rule_name, parser_fn):
        """
        Create a stub parser.

        `rule_name` must be the name of the deferred parser (used for
        pretty-printing).  `parser_fn` must be a callable that returns the
        referenced parser.
        """
        Parser.__init__(self)
        self.rule_name = rule_name
        self.parser_fn = parser_fn
        self._parser = None
        ":type: Parser"

    def get_type(self):
        return self.parser.get_type()

    def generate_code(self, pos_name="pos"):
        return self.parser.gen_code_or_fncall(pos_name=pos_name)


class Transform(Parser):
    """Wrapper parser for a Row parser used to instantiate an AST node."""

    def _is_left_recursive(self, rule_name):
        return self.parser._is_left_recursive(rule_name)

    def __repr__(self):
        return "{0} ^ {1}".format(self.parser, self.typ.name().camel)

    def __init__(self, parser, typ):
        """
        Create a Transform parser wrapping `parser` and that instantiates AST
        nodes whose type is `typ`.
        """
        Parser.__init__(self)
        assert isinstance(typ, ASTNode) or issubclass(typ, ASTNode)

        self.parser = parser
        self.typ = typ
        self._is_ptr = typ.is_ptr

    def children(self):
        return [self.parser]

    def get_type(self):
        return self.typ

    def compute_fields_types(self):
        # Gather field types that come from all child parsers
        fields_types = (
            # There are multiple fields for Row parsers
            [
                parser.get_type()
                for parser in self.parser.parsers
                if not parser.discard()
            ]
            if isinstance(self.parser, Row) else
            [self.parser.get_type()]
        )
        assert all(t for t in fields_types), (
            "Internal error when computing field types for {}:"
            " some are None: {}".format(self.typ, fields_types)
        )

        # Then dispatch these types to all the fields distributed amongst the
        # ASTNode hierarchy.
        for cls in self.typ.get_inheritance_chain():
            fields_count = len(cls.get_parse_fields(include_inherited=False))
            cls.set_types(fields_types[:fields_count])
            fields_types = fields_types[fields_count:]

        Parser.compute_fields_types(self)

    def generate_code(self, pos_name="pos"):

        if isinstance(self.parser, Row):
            self.parser.assign_wrapper(self)

        self.typ.add_to_context()

        parser_context = self.parser.gen_code_or_fncall(pos_name)
        ":type: ParserCodeContext"

        t_env = TemplateEnvironment(
            _self=self,
            # The template needs the compiler context to retrieve the types of
            # the tree fields (required by get_types()).
            parser_context=parser_context,
            args=(
                self.parser.args
                if isinstance(self.parser, Row) else
                [parser_context.res_var_name]
            ),
            res=gen_name("transform_res"),
        )

        return copy_with(
            parser_context,
            res_var_name=t_env.res,
            var_defs=parser_context.var_defs + [
                (t_env.res, self.get_type()),
            ],
            code=render('parsers/transform_code_ada', t_env, pos_name=pos_name)
        )


class Null(Parser):
    """Parser that matches the empty sequence and that yields no AST node."""

    def __init__(self, result_type):
        """
        Create a new Null parser.  `result_type` is either a CompiledType
        subclass that defines what nullexpr this parser returns, either a
        Parser subclass' instance.  In the latter case, this parser will return
        the same type as the other parser.
        """
        Parser.__init__(self)
        if isinstance(result_type, (CompiledType, Parser)):
            self.typ = result_type
        elif issubclass(result_type, CompiledType):
            self.typ = result_type
        else:
            raise TypeError(
                'Invalid result type for Null parser: {}'.format(result_type))

    def children(self):
        return []

    def _is_left_recursive(self, rule_name):
        return False

    def __repr__(self):
        return "Null"

    def generate_code(self, pos_name="pos"):
        typ = self.get_type()
        if isinstance(typ, ASTNode):
            self.get_type().add_to_context()
        res = gen_name("null_res")
        code = render('parsers/null_code_ada', _self=self, res=res)

        return ParserCodeContext(
            pos_name,
            res,
            code,
            [(res, self.get_type())]
        )

    def get_type(self):
        return (
            self.typ.get_type()
            if isinstance(self.typ, Parser) else
            self.typ
        )


class Enum(Parser):
    """Wrapper parser used to returns an enumeration value for an match."""

    def _is_left_recursive(self, rule_name):
        if self.parser:
            return self.parser._is_left_recursive(rule_name)
        return False

    def __repr__(self):
        return "Enum({0}, {1})".format(self.parser, self.enum_type_inst)

    def __init__(self, parser, enum_type_inst):
        """
        Create a wrapper parser around `parser` that returns `enum_type_inst`
        (an EnumType subclass instance) when matching.
        """
        Parser.__init__(self)
        self.parser = resolve(parser) if parser else None
        ":type: Parser|Row"

        self.enum_type_inst = enum_type_inst

    def children(self):
        return []

    def get_type(self):
        return type(self.enum_type_inst)

    def generate_code(self, pos_name="pos"):

        # The sub-parser result will not be used.  We have to notify it if it's
        # a Row so it does not try to generate an anonymous row type.
        if isinstance(self.parser, Row):
            self.parser.assign_wrapper(self)

        self.enum_type_inst.add_to_context()

        parser_context = (
            copy(self.parser.gen_code_or_fncall(pos_name))
            if self.parser
            else ParserCodeContext(
                pos_var_name=pos_name,
                res_var_name="",
                code="",
                var_defs=[]
            )
        )

        env = TemplateEnvironment(
            _self=self,
            res=gen_name("enum_res"),
            parser_context=parser_context

        )

        return copy_with(
            parser_context,
            res_var_name=env.res,
            code=render('parsers/enum_code_ada', env),
            var_defs=parser_context.var_defs + [(env.res, self.get_type())]
        )


_ = Discard
