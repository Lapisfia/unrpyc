# Copyright (c) 2014 CensoredUsername
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

from __future__ import unicode_literals

import re

from util import DecompilerBase, WordConcatenator, reconstruct_paraminfo, simple_expression_guard
import codegen

# Main API

def pprint(out_file, ast, indent_level=0, linenumber=1,
           force_multiline_kwargs=True, decompile_python=True,
           decompile_screencode=True, comparable=False,
           skip_indent_until_write=False):
    return SLDecompiler(out_file,
                 force_multiline_kwargs=force_multiline_kwargs, 
                 decompile_python=decompile_python,
                 decompile_screencode=decompile_screencode, comparable=comparable).dump(
                     ast, indent_level, linenumber, skip_indent_until_write)

# implementation

class SLDecompiler(DecompilerBase):
    """
    an object which handles the decompilation of renpy screen language 1 screens to a given stream
    """

    # This dictionary is a mapping of string: unbound_method, which is used to determine
    # what method to call for which statement
    dispatch = {}

    def __init__(self, out_file=None, force_multiline_kwargs=True, decompile_python=True,
                 decompile_screencode=True, comparable=False, indentation="    "):
        super(SLDecompiler, self).__init__(out_file, indentation, comparable)
        self.force_multiline_kwargs = force_multiline_kwargs
        self.decompile_python = decompile_python
        self.decompile_screencode = decompile_screencode

    def dump(self, ast, indent_level=0, linenumber=1, skip_indent_until_write=False):
        self.indent_level = indent_level
        self.linenumber = linenumber
        self.skip_indent_until_write = skip_indent_until_write
        self.print_screen(ast)
        return self.linenumber

    # Entry point functions

    def print_screen(self, ast):
        # Here we do the processing of the screen statement, and we
        # switch over to parsing of the python string representation

        # Print the screen statement and create the block
        self.indent()
        self.write("screen %s" % ast.name)
        # If we have parameters, print them.
        if hasattr(ast, "parameters") and ast.parameters:
            self.write(reconstruct_paraminfo(ast.parameters))

        # If the value for zorder, modal, or variant has a space after it (even
        # the space separating it from the next key), it will end up in the AST.
        # We need to pack as many as possible onto the screen line so that line
        # numbers can match up, without putting a space after a value that
        # wasn't there originally.
        kwargs_for_screen_line = WordConcatenator(True)
        kwargs_for_separate_lines = []
        for key in ('zorder', 'modal', 'variant'):
            value = getattr(ast, key)
            # Non-Unicode strings are default values rather than user-supplied
            # values, so we don't need to write them out.
            if isinstance(value, unicode):
                guarded_value = simple_expression_guard(value)
                if value[-1] != " " or guarded_value != value.strip():
                    kwargs_for_separate_lines.append("%s %s" % (key, guarded_value))
                else:
                    kwargs_for_screen_line.append(key, value)
        # One value without a space can go on the end of the screen line, since
        # no space goes between the last value and the colon.
        if kwargs_for_separate_lines:
            kwargs_for_screen_line.append(kwargs_for_separate_lines.pop(0))
        self.write("%s:" % kwargs_for_screen_line.join())
        self.indent_level += 1
        for i in kwargs_for_separate_lines:
            self.indent()
            self.write(i)

        # Print any keywords
        if ast.tag:
            self.indent()
            self.write("tag %s" % ast.tag)

        if not self.decompile_python:
            self.indent()
            self.write("pass # Screen code not extracted")
            return

        code = codegen.to_source(ast.code.source, self.indentation)

        if self.decompile_screencode:
            lines = [line for line in code.splitlines() if line.strip() != "ui.close()"]
            self.print_nodes('\n'.join(lines))

        else:
            self.indent()
            self.write("python:")

            self.indent_level += 1
            # The first line is always "_1 = (_name, 0)", which gets included
            # even if the python: block is the only thing in the screen. Don't
            # include ours, since if we do, it'll be included twice when
            # recompiled.
            for line in code.splitlines()[1:]:
                self.indent()
                self.write(line)
            self.indent_level -= 1

        self.indent_level -= 1

    def print_nodes(self, code, extra_indent=0):
        # Print a block of statements, splitting it up on one level.
        # The screen language parser emits lines in the shape _0 = (_0, 0) from which indentation can be revealed.
        # It translates roughly to "id = (parent_id, index_in_parent_children)". When parsing a block
        # parse the first header line to find the parent_id, and then split around headers with the same parent id
        # in this block.
        split = code.split('\n', 1)
        if len(split) == 1:
            return
        header, _ = split

        my_id, parent_id, index = self.parse_header(header)
        # split is [garbage, header1, item1, header2, item2, ...]
        split = re.split(r'( *_[0-9]+ = \(_%s, _?[0-9]+\) *\n?)' % parent_id, code)

        self.indent_level += extra_indent
        for i in range(1, len(split), 2):
            self.print_node(split[i], split[i+1])
        self.indent_level -= extra_indent

    def print_node(self, header, code):
        # Here we derermine how to handle a statement.
        # To do this we look at how the first line in the statement code starts, after the header.
        # Then we call the appropriate function as specified in ui_function_dict.
        # If the statement is unknown, we can still emit valid screen code by just
        # stuffing it inside a python block.

        # The for statement has an extra header. we just swallow it here in case it appears.
        # Otherwise the parser is clueless.
        if re.match(r' *_[0-9]+ = 0', code):
            _, code = code.split('\n', 1)

        for statement, func in self.dispatch.iteritems():
            if code.lstrip().startswith(statement):
                func(self, header, code)
                break
        else:
            self.print_python(header, code)
    # Helper printing functions

    def print_arguments(self, args, kwargs, multiline=True):
        if args:
            self.write(" " + " ".join([simple_expression_guard(i) for i in args]))

        # remove renpy-internal kwargs
        kwargs = [(key, simple_expression_guard(value)) for key, value in kwargs if not
                  (key == 'id' and value.startswith("_") or
                   key == 'scope' and value == '_scope')]

        if self.force_multiline_kwargs and not self.comparable and kwargs:
            self.write(":")
            self.indent_level += 1
            for key, value in kwargs:
                self.indent()
                self.write("%s %s" % (key, value))
            self.indent_level -= 1
        else:
            for key, value in kwargs:
                self.write(" %s %s" % (key, value))
            if multiline:
                self.write(":")

    def print_condition(self, statement, line):
        # This handles parsing of for and if statement conditionals.
        # It also strips the brackets the parser adds around for statement assignments
        # to prevent ren'py from getting a heart attack.

        # Take whatever is between the statement and :
        condition = line.rsplit(":", 1)[0].split(statement, 1)[1].strip()

        if statement == "for":
            variables, expression = condition.split(" in ", 1)
            variables = variables.strip()
            if variables.startswith("(") and variables.endswith(")"):
                # ren'py's for parser is broken
                variables = variables[1:-1]
            condition = "%s in %s" % (variables, expression)
        else:
            condition = condition.strip()
            if condition.startswith("(") and condition.endswith(")"):
                condition = condition[1:-1]
        self.write("%s %s:" % (statement, condition))

    def print_block(self, block):
            # does this statement contain a block or just one statement
            if len(block) > 2 and self.parse_header(block[0]) and self.parse_header(block[1]):
                self.print_nodes('\n'.join(block[1:]), 1)
            elif len(block) > 1 and self.parse_header(block[0]):
                self.print_nodes('\n'.join(block), 1)
            else:
                self.indent_level += 1
                self.indent()
                self.write("pass")
                self.indent_level -= 1

    # Node printing functions

    def print_python(self, header, code):
        # This function handles any statement which is a block but couldn't logically be
        # Translated to a screen statement. If it only contains one line it should not make a block, just use $.
        # Note that because of ui.close() stripping at the start this might not necessarily 
        # still be valid code if we couldn't parse a screen statement containing children.
        self.indent()

        if '\n' in code.strip():
            lines = code.splitlines()
            # Find the first not-whitespace line
            first = next(line for line in lines if line.strip())
            # the indentation is then equal to
            code_indent = len(first) - len(first.lstrip())

            self.write("python:")
            self.indent_level += 1
            for line in lines:
                self.indent()
                self.write(line[code_indent:])
            self.indent_level -= 1
        else:
            self.write("$ %s" % code.strip())

    def print_if(self, header, code):
        # Here we handle the if statement. It might be valid python but we can check for this by
        # checking for the header that should normally occur within the if statement.
        # The if statement parser might also generate a second header if there's more than one screen
        # statement enclosed in the if/elif/else statements. We'll take care of that too.

        # note that it is possible for a python block to have "if" as it's first statement
        # so we check here if a second header appears after the if block to correct this.
        lines = code.splitlines()
        if not self.parse_header(lines[1]):
            # This is not a screenlang if statement, but an if statement in a python block
            return self.print_python(header, code)
        self.indent()

        if_indent = len(lines[0]) - len(lines[0].lstrip())
        current_block = []
        for i, line in enumerate(lines):
            if not i:
                self.print_condition("if", line)
            elif line[if_indent:].startswith("elif"):
                self.print_block(current_block)
                self.indent()
                self.print_condition("elif", line)
                current_block = []
            elif line[if_indent:].startswith("else"):
                self.print_block(current_block)
                self.indent()
                self.write("else:")
                current_block = []
            elif i == len(lines)-1:
                current_block.append(line)
                self.print_block(current_block)
            else:
                current_block.append(line)
    dispatch['if'] = print_if

    def print_for(self, header, code):
        # Here we handle the for statement. Note that the for statement generates some extra python code to 
        # Keep track of it's header indices. The first one is ignored by the statement parser, 
        # the second line is just ingored here. 

        # note that it is possible for a python block to have "for" as it's first statement
        # so we check here if a second header appears after the for block to correct this.
        lines = code.splitlines()
        if not self.parse_header(lines[1]):
            # This is not a screenlang statement
            return self.print_python(header, code)

        self.indent()
        self.print_condition("for", lines[0])
        self.print_block(lines[1:-1])
    dispatch['for'] = print_for

    def print_use(self, header, code):
        # This function handles the use statement, which translates into a python expression "renpy.use_screen".
        # It would technically be possible for this to be a python statement, but the odds of this are very small.
        # renpy itself will insert some kwargs, we'll delete those and then parse the command here.
        args, kwargs, exargs, exkwargs = self.parse_args(code.strip())
        kwargs = [(key, value) for key, value in kwargs if not 
                  (key == '_scope' or key == '_name')]

        self.indent()
        name = args.pop(0)[2:-1]
        self.write("use %s" % name)

        arglist = []
        if args or kwargs or exargs or exkwargs:
            self.write("(")
            arglist.extend(args)
            arglist.extend("%s=%s" % i for i in kwargs)
            if exargs:
                arglist.append("*%s" % exargs)
            if exkwargs:
                arglist.append("**%s" % exkwargs)
            self.write(", ".join(arglist))
            self.write(")")
    dispatch['renpy.use_screen'] = print_use

    def print_default(self, header, code):
        args, _, _, _ = self.parse_args(code.strip())
        key = args[0].split("'", 1)[1].rsplit("'", 1)[0]
        value = args[1]
        self.indent()
        self.write("default %s = %s" % (key, value))
    dispatch['_scope.setdefault'] = print_default

    def print_hotspot(self, header, code):
        line = code.split('\n', 1)[0]
        self.indent()
        self.write("hotspot")
        args, kwargs, _, _ = self.parse_args(line)
        self.print_arguments(args, kwargs)
    dispatch['ui.hotspot_with_child'] = print_hotspot

    def print_ui(self, header, code):
        split = code.split('\n', 1)
        line = split[0]
        block = split[1] if len(split) == 2 else ""
        name = line.split('ui.', 1)[1].split('(', 1)[0]
        self.indent()
        self.write(name)
        args, kwargs, _, _ = self.parse_args(line)
        split = block.split('\n', 1)
        if "ui.child_or_fixed()" in split[0]:
            block = split[1] if len(split) == 2 else ""
        self.print_arguments(args, kwargs, block)
        self.print_nodes(block, 1)
    dispatch['ui.add']          = print_ui
    dispatch['ui.imagebutton']  = print_ui
    dispatch['ui.input']        = print_ui
    dispatch['ui.key']          = print_ui
    dispatch['ui.label']        = print_ui
    dispatch['ui.text']         = print_ui
    dispatch['ui.null']         = print_ui
    dispatch['ui.mousearea']    = print_ui
    dispatch['ui.textbutton']   = print_ui
    dispatch['ui.timer']        = print_ui
    dispatch['ui.bar']          = print_ui
    dispatch['ui.vbar']         = print_ui
    dispatch['ui.hotbar']       = print_ui
    dispatch['ui.button']       = print_ui
    dispatch['ui.frame']        = print_ui
    dispatch['ui.transform']    = print_ui
    dispatch['ui.viewport']     = print_ui
    dispatch['ui.window']       = print_ui
    dispatch['ui.drag']         = print_ui
    dispatch['ui.fixed']        = print_ui
    dispatch['ui.grid']         = print_ui
    dispatch['ui.hbox']         = print_ui
    dispatch['ui.side']         = print_ui
    dispatch['ui.vbox']         = print_ui
    dispatch['ui.imagemap']     = print_ui
    dispatch['ui.draggroup']    = print_ui

    # Parsing functions

    def parse_header(self, header):
        # This parses a pyscreen header into a tuple of id, parent_id, index strings.
        # Note that lowest-level blocks have "_name" as parent
        # instead of a number. after this numbering starts at _1, indexes start at 0
        match = re.search(r' *_([0-9]+) = \(_([0-9]+|name), _?([0-9]+)\) *\n?', header)
        if match:
            return match.group(1), match.group(2), match.group(3)
        else:
            return None

    def count_trailing_slashes(self, split):
        count = 0
        for char in reversed(split):
            if char == '\\':
                count += 1
            else:
                break
        return count

    def parse_args(self, string):
        # This function parses a functionstring, splits it on comma's using splitargs, and then 
        # orders them by args, kwargs, *args and **kwargs.

        # First, we'll split the arguments in a quick and dirty way
        arguments = []

        # If this string contains more than just the arguments, 
        # isolate the part of the string actually containing the arguments
        match = re.match(r'.*?\((.*)\)', string)
        if match:
            string = match.group(1)

        # TODO: support docstrings properly

        stack = [None]
        current_parse = []
        for character in string:
            # Quotes start or end strings
            if character in ("'", '"'):
                # They start them when we're not in a string
                if stack[-1] not in ("'", '"'):
                    stack.append(character)
                # And they end them when there's an even amount of backslashes in front of them
                elif character == stack[-1] and not (self.count_trailing_slashes(current_parse) % 2):
                    stack.pop()
            elif stack[-1] not in ("'", '"'):
                # These characters start a container
                if character in ('[', '(', '{'):
                    stack.append(character)
                # And these close it
                elif character in (']', ')', '}'):
                    # We don't check if they match the entering char since we assume it's valid python
                    stack.pop()
            # If the stack is empty and there's a comma, we have a split
            if len(stack) == 1 and character == ',':
                arguments.append(''.join(current_parse).strip())
                current_parse = []
            else:
                current_parse.append(character)
        # Append the trailing split
        arguments.append(''.join(current_parse).strip())

        # Parse the arguments
        args = []
        kwargs = []
        exargs = None
        exkwargs = None
        for argument in arguments:
            # varname = python_expression
            if re.match('^[a-zA-Z0-9_]+ *=[^=]', argument):
                name, value = argument.split('=', 1)
                kwargs.append((name.strip(), value.strip()))
            elif argument.startswith("**"):
                exkwargs = argument[2:]
            elif argument.startswith("*"):
                exargs = argument[1:]
            else:
                args.append(argument)

        return args, kwargs, exargs, exkwargs
