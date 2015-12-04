# Copyright (c) 2012 Google Inc. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""
TestGyp.py:  a testing framework for GYP integration tests.
"""

import collections
import itertools
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile

import TestCmd
import TestCommon
from TestCommon import __all__

__all__.extend([
  'TestGyp',
])

def remove_debug_line_numbers(contents):
  """Function to remove the line numbers from the debug output
  of gyp and thus remove the exremem fragility of the stdout
  comparison tests.
  """
  lines = contents.splitlines()
  # split each line on ":"
  lines = [l.split(":", 3) for l in lines]
  # join each line back together while ignoring the
  # 3rd column which is the line number
  lines = [len(l) > 3 and ":".join(l[3:]) or l for l in lines]
  return "\n".join(lines)

def match_modulo_line_numbers(contents_a, contents_b):
  """File contents matcher that ignores line numbers."""
  contents_a = remove_debug_line_numbers(contents_a)
  contents_b = remove_debug_line_numbers(contents_b)
  return TestCommon.match_exact(contents_a, contents_b)

class TestGypBase(TestCommon.TestCommon):
  """
  Class for controlling end-to-end tests of gyp generators.

  Instantiating this class will create a temporary directory and
  arrange for its destruction (via the TestCmd superclass) and
  copy all of the non-gyptest files in the directory hierarchy of the
  executing script.

  The default behavior is to test the 'gyp' or 'gyp.bat' file in the
  current directory.  An alternative may be specified explicitly on
  instantiation, or by setting the TESTGYP_GYP environment variable.

  This class should be subclassed for each supported gyp generator
  (format).  Various abstract methods below define calling signatures
  used by the test scripts to invoke builds on the generated build
  configuration and to run executables generated by those builds.
  """

  build_tool = None
  build_tool_list = []

  _exe = TestCommon.exe_suffix
  _obj = TestCommon.obj_suffix
  shobj_ = TestCommon.shobj_prefix
  _shobj = TestCommon.shobj_suffix
  lib_ = TestCommon.lib_prefix
  _lib = TestCommon.lib_suffix
  dll_ = TestCommon.dll_prefix
  _dll = TestCommon.dll_suffix

  # Constants to represent different targets.
  ALL = '__all__'
  DEFAULT = '__default__'

  # Constants for different target types.
  EXECUTABLE = '__executable__'
  STATIC_LIB = '__static_lib__'
  SHARED_LIB = '__shared_lib__'

  def __init__(self, gyp=None, *args, **kw):
    self.origin_cwd = os.path.abspath(os.path.dirname(sys.argv[0]))
    self.extra_args = sys.argv[1:]

    if not gyp:
      gyp = os.environ.get('TESTGYP_GYP')
      if not gyp:
        if sys.platform == 'win32':
          gyp = 'gyp.bat'
        else:
          gyp = 'gyp'
    self.gyp = os.path.abspath(gyp)
    self.no_parallel = False

    self.initialize_build_tool()

    kw.setdefault('match', TestCommon.match_exact)

    # Put test output in out/testworkarea by default.
    # Use temporary names so there are no collisions.
    workdir = os.path.join('out', kw.get('workdir', 'testworkarea'))
    # Create work area if it doesn't already exist.
    if not os.path.isdir(workdir):
      os.makedirs(workdir)

    kw['workdir'] = tempfile.mktemp(prefix='testgyp.', dir=workdir)

    formats = kw.pop('formats', [])

    super(TestGypBase, self).__init__(*args, **kw)

    excluded_formats = set([f for f in formats if f[0] == '!'])
    included_formats = set(formats) - excluded_formats
    if ('!'+self.format in excluded_formats or
        included_formats and self.format not in included_formats):
      msg = 'Invalid test for %r format; skipping test.\n'
      self.skip_test(msg % self.format)

    self.copy_test_configuration(self.origin_cwd, self.workdir)
    self.set_configuration(None)

    # Set $HOME so that gyp doesn't read the user's actual
    # ~/.gyp/include.gypi file, which may contain variables
    # and other settings that would change the output.
    os.environ['HOME'] = self.workpath()
    # Clear $GYP_DEFINES for the same reason.
    if 'GYP_DEFINES' in os.environ:
      del os.environ['GYP_DEFINES']

  def built_file_must_exist(self, name, type=None, **kw):
    """
    Fails the test if the specified built file name does not exist.
    """
    return self.must_exist(self.built_file_path(name, type, **kw))

  def built_file_must_not_exist(self, name, type=None, **kw):
    """
    Fails the test if the specified built file name exists.
    """
    return self.must_not_exist(self.built_file_path(name, type, **kw))

  def built_file_must_match(self, name, contents, **kw):
    """
    Fails the test if the contents of the specified built file name
    do not match the specified contents.
    """
    return self.must_match(self.built_file_path(name, **kw), contents)

  def built_file_must_not_match(self, name, contents, **kw):
    """
    Fails the test if the contents of the specified built file name
    match the specified contents.
    """
    return self.must_not_match(self.built_file_path(name, **kw), contents)

  def built_file_must_not_contain(self, name, contents, **kw):
    """
    Fails the test if the specified built file name contains the specified
    contents.
    """
    return self.must_not_contain(self.built_file_path(name, **kw), contents)

  def copy_test_configuration(self, source_dir, dest_dir):
    """
    Copies the test configuration from the specified source_dir
    (the directory in which the test script lives) to the
    specified dest_dir (a temporary working directory).

    This ignores all files and directories that begin with
    the string 'gyptest', and all '.svn' subdirectories.
    """
    for root, dirs, files in os.walk(source_dir):
      if '.svn' in dirs:
        dirs.remove('.svn')
      dirs = [ d for d in dirs if not d.startswith('gyptest') ]
      files = [ f for f in files if not f.startswith('gyptest') ]
      for dirname in dirs:
        source = os.path.join(root, dirname)
        destination = source.replace(source_dir, dest_dir)
        os.mkdir(destination)
        if sys.platform != 'win32':
          shutil.copystat(source, destination)
      for filename in files:
        source = os.path.join(root, filename)
        destination = source.replace(source_dir, dest_dir)
        shutil.copy2(source, destination)

  def initialize_build_tool(self):
    """
    Initializes the .build_tool attribute.

    Searches the .build_tool_list for an executable name on the user's
    $PATH.  The first tool on the list is used as-is if nothing is found
    on the current $PATH.
    """
    for build_tool in self.build_tool_list:
      if not build_tool:
        continue
      if os.path.isabs(build_tool):
        self.build_tool = build_tool
        return
      build_tool = self.where_is(build_tool)
      if build_tool:
        self.build_tool = build_tool
        return

    if self.build_tool_list:
      self.build_tool = self.build_tool_list[0]

  def relocate(self, source, destination):
    """
    Renames (relocates) the specified source (usually a directory)
    to the specified destination, creating the destination directory
    first if necessary.

    Note:  Don't use this as a generic "rename" operation.  In the
    future, "relocating" parts of a GYP tree may affect the state of
    the test to modify the behavior of later method calls.
    """
    destination_dir = os.path.dirname(destination)
    if not os.path.exists(destination_dir):
      self.subdir(destination_dir)
    os.rename(source, destination)

  def report_not_up_to_date(self):
    """
    Reports that a build is not up-to-date.

    This provides common reporting for formats that have complicated
    conditions for checking whether a build is up-to-date.  Formats
    that expect exact output from the command (make) can
    just set stdout= when they call the run_build() method.
    """
    print "Build is not up-to-date:"
    print self.banner('STDOUT ')
    print self.stdout()
    stderr = self.stderr()
    if stderr:
      print self.banner('STDERR ')
      print stderr

  def run_gyp(self, gyp_file, *args, **kw):
    """
    Runs gyp against the specified gyp_file with the specified args.
    """

    # When running gyp, and comparing its output we use a comparitor
    # that ignores the line numbers that gyp logs in its debug output.
    if kw.pop('ignore_line_numbers', False):
      kw.setdefault('match', match_modulo_line_numbers)

    # TODO:  --depth=. works around Chromium-specific tree climbing.
    depth = kw.pop('depth', '.')
    run_args = ['--depth='+depth, '--format='+self.format, gyp_file]
    if self.no_parallel:
      run_args += ['--no-parallel']
    run_args.extend(self.extra_args)
    run_args.extend(args)
    return self.run(program=self.gyp, arguments=run_args, **kw)

  def run(self, *args, **kw):
    """
    Executes a program by calling the superclass .run() method.

    This exists to provide a common place to filter out keyword
    arguments implemented in this layer, without having to update
    the tool-specific subclasses or clutter the tests themselves
    with platform-specific code.
    """
    if kw.has_key('SYMROOT'):
      del kw['SYMROOT']
    super(TestGypBase, self).run(*args, **kw)

  def set_configuration(self, configuration):
    """
    Sets the configuration, to be used for invoking the build
    tool and testing potential built output.
    """
    self.configuration = configuration

  def configuration_dirname(self):
    if self.configuration:
      return self.configuration.split('|')[0]
    else:
      return 'Default'

  def configuration_buildname(self):
    if self.configuration:
      return self.configuration
    else:
      return 'Default'

  #
  # Abstract methods to be defined by format-specific subclasses.
  #

  def build(self, gyp_file, target=None, **kw):
    """
    Runs a build of the specified target against the configuration
    generated from the specified gyp_file.

    A 'target' argument of None or the special value TestGyp.DEFAULT
    specifies the default argument for the underlying build tool.
    A 'target' argument of TestGyp.ALL specifies the 'all' target
    (if any) of the underlying build tool.
    """
    raise NotImplementedError

  def built_file_path(self, name, type=None, **kw):
    """
    Returns a path to the specified file name, of the specified type.
    """
    raise NotImplementedError

  def built_file_basename(self, name, type=None, **kw):
    """
    Returns the base name of the specified file name, of the specified type.

    A bare=True keyword argument specifies that prefixes and suffixes shouldn't
    be applied.
    """
    if not kw.get('bare'):
      if type == self.EXECUTABLE:
        name = name + self._exe
      elif type == self.STATIC_LIB:
        name = self.lib_ + name + self._lib
      elif type == self.SHARED_LIB:
        name = self.dll_ + name + self._dll
    return name

  def run_built_executable(self, name, *args, **kw):
    """
    Runs an executable program built from a gyp-generated configuration.

    The specified name should be independent of any particular generator.
    Subclasses should find the output executable in the appropriate
    output build directory, tack on any necessary executable suffix, etc.
    """
    raise NotImplementedError

  def up_to_date(self, gyp_file, target=None, **kw):
    """
    Verifies that a build of the specified target is up to date.

    The subclass should implement this by calling build()
    (or a reasonable equivalent), checking whatever conditions
    will tell it the build was an "up to date" null build, and
    failing if it isn't.
    """
    raise NotImplementedError


class TestGypGypd(TestGypBase):
  """
  Subclass for testing the GYP 'gypd' generator (spit out the
  internal data structure as pretty-printed Python).
  """
  format = 'gypd'
  def __init__(self, gyp=None, *args, **kw):
    super(TestGypGypd, self).__init__(*args, **kw)
    # gypd implies the use of 'golden' files, so parallelizing conflicts as it
    # causes ordering changes.
    self.no_parallel = True


class TestGypCustom(TestGypBase):
  """
  Subclass for testing the GYP with custom generator
  """

  def __init__(self, gyp=None, *args, **kw):
    self.format = kw.pop("format")
    super(TestGypCustom, self).__init__(*args, **kw)


class TestGypAndroid(TestGypBase):
  """
  Subclass for testing the GYP Android makefile generator. Note that
  build/envsetup.sh and lunch must have been run before running tests.

  TODO: This is currently an incomplete implementation. We do not support
  run_built_executable(), so we pass only tests which do not use this. As a
  result, support for host targets is not properly tested.
  """
  format = 'android'

  # Note that we can't use mmm as the build tool because ...
  # - it builds all targets, whereas we need to pass a target
  # - it is a function, whereas the test runner assumes the build tool is a file
  # Instead we use make and duplicate the logic from mmm.
  build_tool_list = ['make']

  # We use our custom target 'gyp_all_modules', as opposed to the 'all_modules'
  # target used by mmm, to build only those targets which are part of the gyp
  # target 'all'.
  ALL = 'gyp_all_modules'

  def __init__(self, gyp=None, *args, **kw):
    # Android requires build and test output to be inside its source tree.
    # We use the following working directory for the test's source, but the
    # test's build output still goes to $ANDROID_PRODUCT_OUT.
    # Note that some tests explicitly set format='gypd' to invoke the gypd
    # backend. This writes to the source tree, but there's no way around this.
    kw['workdir'] = os.path.join('/tmp', 'gyptest',
                                 kw.get('workdir', 'testworkarea'))
    # We need to remove all gyp outputs from out/. Ths is because some tests
    # don't have rules to regenerate output, so they will simply re-use stale
    # output if present. Since the test working directory gets regenerated for
    # each test run, this can confuse things.
    # We don't have a list of build outputs because we don't know which
    # dependent targets were built. Instead we delete all gyp-generated output.
    # This may be excessive, but should be safe.
    out_dir = os.environ['ANDROID_PRODUCT_OUT']
    obj_dir = os.path.join(out_dir, 'obj')
    shutil.rmtree(os.path.join(obj_dir, 'GYP'), ignore_errors = True)
    for x in ['EXECUTABLES', 'STATIC_LIBRARIES', 'SHARED_LIBRARIES']:
      for d in os.listdir(os.path.join(obj_dir, x)):
        if d.endswith('_gyp_intermediates'):
          shutil.rmtree(os.path.join(obj_dir, x, d), ignore_errors = True)
    for x in [os.path.join('obj', 'lib'), os.path.join('system', 'lib')]:
      for d in os.listdir(os.path.join(out_dir, x)):
        if d.endswith('_gyp.so'):
          os.remove(os.path.join(out_dir, x, d))

    super(TestGypAndroid, self).__init__(*args, **kw)

  def target_name(self, target):
    if target == self.ALL:
      return self.ALL
    # The default target is 'droid'. However, we want to use our special target
    # to build only the gyp target 'all'.
    if target in (None, self.DEFAULT):
      return self.ALL
    return target

  def build(self, gyp_file, target=None, **kw):
    """
    Runs a build using the Android makefiles generated from the specified
    gyp_file. This logic is taken from Android's mmm.
    """
    arguments = kw.get('arguments', [])[:]
    arguments.append(self.target_name(target))
    arguments.append('-C')
    arguments.append(os.environ['ANDROID_BUILD_TOP'])
    kw['arguments'] = arguments
    chdir = kw.get('chdir', '')
    makefile = os.path.join(self.workdir, chdir, 'GypAndroid.mk')
    os.environ['ONE_SHOT_MAKEFILE'] = makefile
    result = self.run(program=self.build_tool, **kw)
    del os.environ['ONE_SHOT_MAKEFILE']
    return result

  def android_module(self, group, name, subdir):
    if subdir:
      name = '%s_%s' % (subdir, name)
    if group == 'SHARED_LIBRARIES':
      name = 'lib_%s' % name
    return '%s_gyp' % name

  def intermediates_dir(self, group, module_name):
    return os.path.join(os.environ['ANDROID_PRODUCT_OUT'], 'obj', group,
                        '%s_intermediates' % module_name)

  def built_file_path(self, name, type=None, **kw):
    """
    Returns a path to the specified file name, of the specified type,
    as built by Android. Note that we don't support the configuration
    parameter.
    """
    # Built files are in $ANDROID_PRODUCT_OUT. This requires copying logic from
    # the Android build system.
    if type == None:
      return os.path.join(os.environ['ANDROID_PRODUCT_OUT'], 'obj', 'GYP',
                          'shared_intermediates', name)
    subdir = kw.get('subdir')
    if type == self.EXECUTABLE:
      # We don't install executables
      group = 'EXECUTABLES'
      module_name = self.android_module(group, name, subdir)
      return os.path.join(self.intermediates_dir(group, module_name), name)
    if type == self.STATIC_LIB:
      group = 'STATIC_LIBRARIES'
      module_name = self.android_module(group, name, subdir)
      return os.path.join(self.intermediates_dir(group, module_name),
                          '%s.a' % module_name)
    if type == self.SHARED_LIB:
      group = 'SHARED_LIBRARIES'
      module_name = self.android_module(group, name, subdir)
      return os.path.join(self.intermediates_dir(group, module_name), 'LINKED',
                          '%s.so' % module_name)
    assert False, 'Unhandled type'

  def run_built_executable(self, name, *args, **kw):
    """
    Runs an executable program built from a gyp-generated configuration.

    This is not correctly implemented for Android. For now, we simply check
    that the executable file exists.
    """
    # Running executables requires a device. Even if we build for target x86,
    # the binary is not built with the correct toolchain options to actually
    # run on the host.

    # Copied from TestCommon.run()
    match = kw.pop('match', self.match)
    status = None
    if os.path.exists(self.built_file_path(name)):
      status = 1
    self._complete(None, None, None, None, status, match)

  def match_single_line(self, lines = None, expected_line = None):
    """
    Checks that specified line appears in the text.
    """
    for line in lines.split('\n'):
        if line == expected_line:
            return 1
    return

  def up_to_date(self, gyp_file, target=None, **kw):
    """
    Verifies that a build of the specified target is up to date.
    """
    kw['stdout'] = ("make: Nothing to be done for `%s'." %
                    self.target_name(target))

    # We need to supply a custom matcher, since we don't want to depend on the
    # exact stdout string.
    kw['match'] = self.match_single_line
    return self.build(gyp_file, target, **kw)


class TestGypCMake(TestGypBase):
  """
  Subclass for testing the GYP CMake generator, using cmake's ninja backend.
  """
  format = 'cmake'
  build_tool_list = ['cmake']
  ALL = 'all'

  def cmake_build(self, gyp_file, target=None, **kw):
    arguments = kw.get('arguments', [])[:]

    self.build_tool_list = ['cmake']
    self.initialize_build_tool()

    chdir = os.path.join(kw.get('chdir', '.'),
                         'out',
                         self.configuration_dirname())
    kw['chdir'] = chdir

    arguments.append('-G')
    arguments.append('Ninja')

    kw['arguments'] = arguments

    stderr = kw.get('stderr', None)
    if stderr:
      kw['stderr'] = stderr.split('$$$')[0]

    self.run(program=self.build_tool, **kw)

  def ninja_build(self, gyp_file, target=None, **kw):
    arguments = kw.get('arguments', [])[:]

    self.build_tool_list = ['ninja']
    self.initialize_build_tool()

    # Add a -C output/path to the command line.
    arguments.append('-C')
    arguments.append(os.path.join('out', self.configuration_dirname()))

    if target not in (None, self.DEFAULT):
      arguments.append(target)

    kw['arguments'] = arguments

    stderr = kw.get('stderr', None)
    if stderr:
      stderrs = stderr.split('$$$')
      kw['stderr'] = stderrs[1] if len(stderrs) > 1 else ''

    return self.run(program=self.build_tool, **kw)

  def build(self, gyp_file, target=None, status=0, **kw):
    # Two tools must be run to build, cmake and the ninja.
    # Allow cmake to succeed when the overall expectation is to fail.
    if status is None:
      kw['status'] = None
    else:
      if not isinstance(status, collections.Iterable): status = (status,)
      kw['status'] = list(itertools.chain((0,), status))
    self.cmake_build(gyp_file, target, **kw)
    kw['status'] = status
    self.ninja_build(gyp_file, target, **kw)

  def run_built_executable(self, name, *args, **kw):
    # Enclosing the name in a list avoids prepending the original dir.
    program = [self.built_file_path(name, type=self.EXECUTABLE, **kw)]
    if sys.platform == 'darwin':
      configuration = self.configuration_dirname()
      os.environ['DYLD_LIBRARY_PATH'] = os.path.join('out', configuration)
    return self.run(program=program, *args, **kw)

  def built_file_path(self, name, type=None, **kw):
    result = []
    chdir = kw.get('chdir')
    if chdir:
      result.append(chdir)
    result.append('out')
    result.append(self.configuration_dirname())
    if type == self.STATIC_LIB:
      if sys.platform != 'darwin':
        result.append('obj.target')
    elif type == self.SHARED_LIB:
      if sys.platform != 'darwin' and sys.platform != 'win32':
        result.append('lib.target')
    subdir = kw.get('subdir')
    if subdir and type != self.SHARED_LIB:
      result.append(subdir)
    result.append(self.built_file_basename(name, type, **kw))
    return self.workpath(*result)

  def up_to_date(self, gyp_file, target=None, **kw):
    result = self.ninja_build(gyp_file, target, **kw)
    if not result:
      stdout = self.stdout()
      if 'ninja: no work to do' not in stdout:
        self.report_not_up_to_date()
        self.fail_test()
    return result


class TestGypMake(TestGypBase):
  """
  Subclass for testing the GYP Make generator.
  """
  format = 'make'
  build_tool_list = ['make']
  ALL = 'all'
  def build(self, gyp_file, target=None, **kw):
    """
    Runs a Make build using the Makefiles generated from the specified
    gyp_file.
    """
    arguments = kw.get('arguments', [])[:]
    if self.configuration:
      arguments.append('BUILDTYPE=' + self.configuration)
    if target not in (None, self.DEFAULT):
      arguments.append(target)
    # Sub-directory builds provide per-gyp Makefiles (i.e.
    # Makefile.gyp_filename), so use that if there is no Makefile.
    chdir = kw.get('chdir', '')
    if not os.path.exists(os.path.join(chdir, 'Makefile')):
      print "NO Makefile in " + os.path.join(chdir, 'Makefile')
      arguments.insert(0, '-f')
      arguments.insert(1, os.path.splitext(gyp_file)[0] + '.Makefile')
    kw['arguments'] = arguments
    return self.run(program=self.build_tool, **kw)
  def up_to_date(self, gyp_file, target=None, **kw):
    """
    Verifies that a build of the specified Make target is up to date.
    """
    if target in (None, self.DEFAULT):
      message_target = 'all'
    else:
      message_target = target
    kw['stdout'] = "make: Nothing to be done for `%s'.\n" % message_target
    return self.build(gyp_file, target, **kw)
  def run_built_executable(self, name, *args, **kw):
    """
    Runs an executable built by Make.
    """
    configuration = self.configuration_dirname()
    libdir = os.path.join('out', configuration, 'lib')
    # TODO(piman): when everything is cross-compile safe, remove lib.target
    if sys.platform == 'darwin':
      # Mac puts target shared libraries right in the product directory.
      configuration = self.configuration_dirname()
      os.environ['DYLD_LIBRARY_PATH'] = (
          libdir + '.host:' + os.path.join('out', configuration))
    else:
      os.environ['LD_LIBRARY_PATH'] = libdir + '.host:' + libdir + '.target'
    # Enclosing the name in a list avoids prepending the original dir.
    program = [self.built_file_path(name, type=self.EXECUTABLE, **kw)]
    return self.run(program=program, *args, **kw)
  def built_file_path(self, name, type=None, **kw):
    """
    Returns a path to the specified file name, of the specified type,
    as built by Make.

    Built files are in the subdirectory 'out/{configuration}'.
    The default is 'out/Default'.

    A chdir= keyword argument specifies the source directory
    relative to which  the output subdirectory can be found.

    "type" values of STATIC_LIB or SHARED_LIB append the necessary
    prefixes and suffixes to a platform-independent library base name.

    A subdir= keyword argument specifies a library subdirectory within
    the default 'obj.target'.
    """
    result = []
    chdir = kw.get('chdir')
    if chdir:
      result.append(chdir)
    configuration = self.configuration_dirname()
    result.extend(['out', configuration])
    if type == self.STATIC_LIB and sys.platform != 'darwin':
      result.append('obj.target')
    elif type == self.SHARED_LIB and sys.platform != 'darwin':
      result.append('lib.target')
    subdir = kw.get('subdir')
    if subdir and type != self.SHARED_LIB:
      result.append(subdir)
    result.append(self.built_file_basename(name, type, **kw))
    return self.workpath(*result)


def ConvertToCygpath(path):
  """Convert to cygwin path if we are using cygwin."""
  if sys.platform == 'cygwin':
    p = subprocess.Popen(['cygpath', path], stdout=subprocess.PIPE)
    path = p.communicate()[0].strip()
  return path


def FindVisualStudioInstallation():
  """Returns appropriate values for .build_tool and .uses_msbuild fields
  of TestGypBase for Visual Studio.

  We use the value specified by GYP_MSVS_VERSION.  If not specified, we
  search %PATH% and %PATHEXT% for a devenv.{exe,bat,...} executable.
  Failing that, we search for likely deployment paths.
  """
  possible_roots = ['%s:\\Program Files%s' % (chr(drive), suffix)
                    for drive in range(ord('C'), ord('Z') + 1)
                    for suffix in ['', ' (x86)']]
  possible_paths = {
      '2013': r'Microsoft Visual Studio 12.0\Common7\IDE\devenv.com',
      '2012': r'Microsoft Visual Studio 11.0\Common7\IDE\devenv.com',
      '2010': r'Microsoft Visual Studio 10.0\Common7\IDE\devenv.com',
      '2008': r'Microsoft Visual Studio 9.0\Common7\IDE\devenv.com',
      '2005': r'Microsoft Visual Studio 8\Common7\IDE\devenv.com'}

  possible_roots = [ConvertToCygpath(r) for r in possible_roots]

  msvs_version = 'auto'
  for flag in (f for f in sys.argv if f.startswith('msvs_version=')):
    msvs_version = flag.split('=')[-1]
  msvs_version = os.environ.get('GYP_MSVS_VERSION', msvs_version)

  build_tool = None
  if msvs_version in possible_paths:
    # Check that the path to the specified GYP_MSVS_VERSION exists.
    path = possible_paths[msvs_version]
    for r in possible_roots:
      bt = os.path.join(r, path)
      if os.path.exists(bt):
        build_tool = bt
        uses_msbuild = msvs_version >= '2010'
        return build_tool, uses_msbuild
    else:
      print ('Warning: Environment variable GYP_MSVS_VERSION specifies "%s" '
              'but corresponding "%s" was not found.' % (msvs_version, path))
  if build_tool:
    # We found 'devenv' on the path, use that and try to guess the version.
    for version, path in possible_paths.iteritems():
      if build_tool.find(path) >= 0:
        uses_msbuild = version >= '2010'
        return build_tool, uses_msbuild
    else:
      # If not, assume not MSBuild.
      uses_msbuild = False
    return build_tool, uses_msbuild
  # Neither GYP_MSVS_VERSION nor the path help us out.  Iterate through
  # the choices looking for a match.
  for version in sorted(possible_paths, reverse=True):
    path = possible_paths[version]
    for r in possible_roots:
      bt = os.path.join(r, path)
      if os.path.exists(bt):
        build_tool = bt
        uses_msbuild = msvs_version >= '2010'
        return build_tool, uses_msbuild
  print 'Error: could not find devenv'
  sys.exit(1)

class TestGypOnMSToolchain(TestGypBase):
  """
  Common subclass for testing generators that target the Microsoft Visual
  Studio toolchain (cl, link, dumpbin, etc.)
  """
  @staticmethod
  def _ComputeVsvarsPath(devenv_path):
    devenv_dir = os.path.split(devenv_path)[0]
    vsvars_path = os.path.join(devenv_path, '../../Tools/vsvars32.bat')
    return vsvars_path

  def initialize_build_tool(self):
    super(TestGypOnMSToolchain, self).initialize_build_tool()
    if sys.platform in ('win32', 'cygwin'):
      self.devenv_path, self.uses_msbuild = FindVisualStudioInstallation()
      self.vsvars_path = TestGypOnMSToolchain._ComputeVsvarsPath(
          self.devenv_path)

  def run_dumpbin(self, *dumpbin_args):
    """Run the dumpbin tool with the specified arguments, and capturing and
    returning stdout."""
    assert sys.platform in ('win32', 'cygwin')
    cmd = os.environ.get('COMSPEC', 'cmd.exe')
    arguments = [cmd, '/c', self.vsvars_path, '&&', 'dumpbin']
    arguments.extend(dumpbin_args)
    proc = subprocess.Popen(arguments, stdout=subprocess.PIPE)
    output = proc.communicate()[0]
    assert not proc.returncode
    return output

class TestGypNinja(TestGypOnMSToolchain):
  """
  Subclass for testing the GYP Ninja generator.
  """
  format = 'ninja'
  build_tool_list = ['ninja']
  ALL = 'all'
  DEFAULT = 'all'

  def run_gyp(self, gyp_file, *args, **kw):
    TestGypBase.run_gyp(self, gyp_file, *args, **kw)

  def build(self, gyp_file, target=None, **kw):
    arguments = kw.get('arguments', [])[:]

    # Add a -C output/path to the command line.
    arguments.append('-C')
    arguments.append(os.path.join('out', self.configuration_dirname()))

    if target is None:
      target = 'all'
    arguments.append(target)

    kw['arguments'] = arguments
    return self.run(program=self.build_tool, **kw)

  def run_built_executable(self, name, *args, **kw):
    # Enclosing the name in a list avoids prepending the original dir.
    program = [self.built_file_path(name, type=self.EXECUTABLE, **kw)]
    if sys.platform == 'darwin':
      configuration = self.configuration_dirname()
      os.environ['DYLD_LIBRARY_PATH'] = os.path.join('out', configuration)
    return self.run(program=program, *args, **kw)

  def built_file_path(self, name, type=None, **kw):
    result = []
    chdir = kw.get('chdir')
    if chdir:
      result.append(chdir)
    result.append('out')
    result.append(self.configuration_dirname())
    if type == self.STATIC_LIB:
      if sys.platform != 'darwin':
        result.append('obj')
    elif type == self.SHARED_LIB:
      if sys.platform != 'darwin' and sys.platform != 'win32':
        result.append('lib')
    subdir = kw.get('subdir')
    if subdir and type != self.SHARED_LIB:
      result.append(subdir)
    result.append(self.built_file_basename(name, type, **kw))
    return self.workpath(*result)

  def up_to_date(self, gyp_file, target=None, **kw):
    result = self.build(gyp_file, target, **kw)
    if not result:
      stdout = self.stdout()
      if 'ninja: no work to do' not in stdout:
        self.report_not_up_to_date()
        self.fail_test()
    return result


class TestGypMSVS(TestGypOnMSToolchain):
  """
  Subclass for testing the GYP Visual Studio generator.
  """
  format = 'msvs'

  u = r'=== Build: 0 succeeded, 0 failed, (\d+) up-to-date, 0 skipped ==='
  up_to_date_re = re.compile(u, re.M)

  # Initial None element will indicate to our .initialize_build_tool()
  # method below that 'devenv' was not found on %PATH%.
  #
  # Note:  we must use devenv.com to be able to capture build output.
  # Directly executing devenv.exe only sends output to BuildLog.htm.
  build_tool_list = [None, 'devenv.com']

  def initialize_build_tool(self):
    super(TestGypMSVS, self).initialize_build_tool()
    self.build_tool = self.devenv_path

  def build(self, gyp_file, target=None, rebuild=False, clean=False, **kw):
    """
    Runs a Visual Studio build using the configuration generated
    from the specified gyp_file.
    """
    configuration = self.configuration_buildname()
    if clean:
      build = '/Clean'
    elif rebuild:
      build = '/Rebuild'
    else:
      build = '/Build'
    arguments = kw.get('arguments', [])[:]
    arguments.extend([gyp_file.replace('.gyp', '.sln'),
                      build, configuration])
    # Note:  the Visual Studio generator doesn't add an explicit 'all'
    # target, so we just treat it the same as the default.
    if target not in (None, self.ALL, self.DEFAULT):
      arguments.extend(['/Project', target])
    if self.configuration:
      arguments.extend(['/ProjectConfig', self.configuration])
    kw['arguments'] = arguments
    return self.run(program=self.build_tool, **kw)
  def up_to_date(self, gyp_file, target=None, **kw):
    """
    Verifies that a build of the specified Visual Studio target is up to date.

    Beware that VS2010 will behave strangely if you build under
    C:\USERS\yourname\AppData\Local. It will cause needless work.  The ouptut
    will be "1 succeeded and 0 up to date".  MSBuild tracing reveals that:
    "Project 'C:\Users\...\AppData\Local\...vcxproj' not up to date because
    'C:\PROGRAM FILES (X86)\MICROSOFT VISUAL STUDIO 10.0\VC\BIN\1033\CLUI.DLL'
    was modified at 02/21/2011 17:03:30, which is newer than '' which was
    modified at 01/01/0001 00:00:00.

    The workaround is to specify a workdir when instantiating the test, e.g.
    test = TestGyp.TestGyp(workdir='workarea')
    """
    result = self.build(gyp_file, target, **kw)
    if not result:
      stdout = self.stdout()

      m = self.up_to_date_re.search(stdout)
      up_to_date = m and int(m.group(1)) > 0
      if not up_to_date:
        self.report_not_up_to_date()
        self.fail_test()
    return result
  def run_built_executable(self, name, *args, **kw):
    """
    Runs an executable built by Visual Studio.
    """
    configuration = self.configuration_dirname()
    # Enclosing the name in a list avoids prepending the original dir.
    program = [self.built_file_path(name, type=self.EXECUTABLE, **kw)]
    return self.run(program=program, *args, **kw)
  def built_file_path(self, name, type=None, **kw):
    """
    Returns a path to the specified file name, of the specified type,
    as built by Visual Studio.

    Built files are in a subdirectory that matches the configuration
    name.  The default is 'Default'.

    A chdir= keyword argument specifies the source directory
    relative to which  the output subdirectory can be found.

    "type" values of STATIC_LIB or SHARED_LIB append the necessary
    prefixes and suffixes to a platform-independent library base name.
    """
    result = []
    chdir = kw.get('chdir')
    if chdir:
      result.append(chdir)
    result.append(self.configuration_dirname())
    if type == self.STATIC_LIB:
      result.append('lib')
    result.append(self.built_file_basename(name, type, **kw))
    return self.workpath(*result)


class TestGypXcode(TestGypBase):
  """
  Subclass for testing the GYP Xcode generator.
  """
  format = 'xcode'
  build_tool_list = ['xcodebuild']

  phase_script_execution = ("\n"
                            "PhaseScriptExecution /\\S+/Script-[0-9A-F]+\\.sh\n"
                            "    cd /\\S+\n"
                            "    /bin/sh -c /\\S+/Script-[0-9A-F]+\\.sh\n"
                            "(make: Nothing to be done for `all'\\.\n)?")

  strip_up_to_date_expressions = [
    # Various actions or rules can run even when the overall build target
    # is up to date.  Strip those phases' GYP-generated output.
    re.compile(phase_script_execution, re.S),

    # The message from distcc_pump can trail the "BUILD SUCCEEDED"
    # message, so strip that, too.
    re.compile('__________Shutting down distcc-pump include server\n', re.S),
  ]

  up_to_date_endings = (
    'Checking Dependencies...\n** BUILD SUCCEEDED **\n', # Xcode 3.0/3.1
    'Check dependencies\n** BUILD SUCCEEDED **\n\n',     # Xcode 3.2
    'Check dependencies\n\n\n** BUILD SUCCEEDED **\n\n', # Xcode 4.2
    'Check dependencies\n\n** BUILD SUCCEEDED **\n\n',   # Xcode 5.0
  )

  def build(self, gyp_file, target=None, **kw):
    """
    Runs an xcodebuild using the .xcodeproj generated from the specified
    gyp_file.
    """
    # Be sure we're working with a copy of 'arguments' since we modify it.
    # The caller may not be expecting it to be modified.
    arguments = kw.get('arguments', [])[:]
    arguments.extend(['-project', gyp_file.replace('.gyp', '.xcodeproj')])
    if target == self.ALL:
      arguments.append('-alltargets',)
    elif target not in (None, self.DEFAULT):
      arguments.extend(['-target', target])
    if self.configuration:
      arguments.extend(['-configuration', self.configuration])
    symroot = kw.get('SYMROOT', '$SRCROOT/build')
    if symroot:
      arguments.append('SYMROOT='+symroot)
    kw['arguments'] = arguments

    # Work around spurious stderr output from Xcode 4, http://crbug.com/181012
    match = kw.pop('match', self.match)
    def match_filter_xcode(actual, expected):
      if actual:
        if not TestCmd.is_List(actual):
          actual = actual.split('\n')
        if not TestCmd.is_List(expected):
          expected = expected.split('\n')
        actual = [a for a in actual
                    if 'No recorder, buildTask: <Xcode3BuildTask:' not in a]
      return match(actual, expected)
    kw['match'] = match_filter_xcode

    return self.run(program=self.build_tool, **kw)
  def up_to_date(self, gyp_file, target=None, **kw):
    """
    Verifies that a build of the specified Xcode target is up to date.
    """
    result = self.build(gyp_file, target, **kw)
    if not result:
      output = self.stdout()
      for expression in self.strip_up_to_date_expressions:
        output = expression.sub('', output)
      if not output.endswith(self.up_to_date_endings):
        self.report_not_up_to_date()
        self.fail_test()
    return result
  def run_built_executable(self, name, *args, **kw):
    """
    Runs an executable built by xcodebuild.
    """
    configuration = self.configuration_dirname()
    os.environ['DYLD_LIBRARY_PATH'] = os.path.join('build', configuration)
    # Enclosing the name in a list avoids prepending the original dir.
    program = [self.built_file_path(name, type=self.EXECUTABLE, **kw)]
    return self.run(program=program, *args, **kw)
  def built_file_path(self, name, type=None, **kw):
    """
    Returns a path to the specified file name, of the specified type,
    as built by Xcode.

    Built files are in the subdirectory 'build/{configuration}'.
    The default is 'build/Default'.

    A chdir= keyword argument specifies the source directory
    relative to which  the output subdirectory can be found.

    "type" values of STATIC_LIB or SHARED_LIB append the necessary
    prefixes and suffixes to a platform-independent library base name.
    """
    result = []
    chdir = kw.get('chdir')
    if chdir:
      result.append(chdir)
    configuration = self.configuration_dirname()
    result.extend(['build', configuration])
    result.append(self.built_file_basename(name, type, **kw))
    return self.workpath(*result)


format_class_list = [
  TestGypGypd,
  TestGypAndroid,
  TestGypCMake,
  TestGypMake,
  TestGypMSVS,
  TestGypNinja,
  TestGypXcode,
]

def TestGyp(*args, **kw):
  """
  Returns an appropriate TestGyp* instance for a specified GYP format.
  """
  format = kw.pop('format', os.environ.get('TESTGYP_FORMAT'))
  for format_class in format_class_list:
    if format == format_class.format:
      return format_class(*args, **kw)
  raise Exception, "unknown format %r" % format
