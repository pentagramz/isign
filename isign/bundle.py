""" Represents a bundle. In the words of the Apple docs, it's a convenient way to deliver
    software. Really it's a particular kind of directory structure, with one main executable,
    well-known places for various data files and libraries,
    and tracking hashes of all those files for signing purposes.

    For isign, we have two main kinds of bundles: the App, and the Framework (a reusable
    library packaged along with its data files.) An App may contain many Frameworks, but
    a Framework has to be re-signed independently.

    See the Apple Developer Documentation "About Bundles" """

import biplist
import code_resources
from exceptions import NotMatched
import glob
import logging
import os
from os.path import basename, exists, join, splitext
import signable
import shutil


log = logging.getLogger(__name__)


def is_info_plist_native(plist):
    """ If an bundle is for native iOS, it has these properties in the Info.plist """
    return (
        'CFBundleSupportedPlatforms' in plist and
        'iPhoneOS' in plist['CFBundleSupportedPlatforms']
    )


class Bundle(object):
    """ A bundle is a standard directory structure, a signable, installable set of files.
        Apps are Bundles, but so are some kinds of Frameworks (libraries) """
    helpers = []
    executable_class = None

    def __init__(self, path):
        self.path = path
        is_native = self.precheck()
        if not is_native:
            raise NotMatched("not a native iOS bundle")

        # will be added later
        self.seal_path = None

        info_path = join(self.path, 'Info.plist')
        self.info = biplist.readPlist(info_path)

    def precheck(self):
        """ Checks if a path looks like this kind of app,
            return stuff we'll need to know about its structure """
        is_native = False
        plist_path = join(self.path, "Info.plist")
        if exists(plist_path):
            log.debug("got a plist path, {}".format(plist_path))
            plist = biplist.readPlist(plist_path)
            is_native = is_info_plist_native(plist)
            log.debug("is native: {}".format(is_native))
        return is_native

    def get_executable_path(self):
        """ Path to the main executable. For an app, this is app itself. For
            a Framework, this is the main framework """
        executable_name = None
        if 'CFBundleExecutable' in self.info:
            executable_name = self.info['CFBundleExecutable']
        else:
            executable_name, _ = splitext(basename(self.path))
        executable = join(self.path, executable_name)
        if not exists(executable):
            raise Exception(
                'could not find executable for {0}'.format(self.path))
        return executable

    def sign(self, signer):
        """ Sign everything in this bundle, recursively with sub-bundles """
        # log.debug("SIGNING: %s" % self.path)
        frameworks_path = join(self.path, 'Frameworks')
        if exists(frameworks_path):
            # log.debug("SIGNING FRAMEWORKS: %s" % frameworks_path)
            # sign all the frameworks
            for framework_name in os.listdir(frameworks_path):
                framework_path = join(frameworks_path, framework_name)
                # log.debug("checking for framework: %s" % framework_path)
                try:
                    framework = Framework(framework_path)
                    # log.debug("resigning: %s" % framework_path)
                    framework.resign(signer)
                except NotMatched:
                    # log.debug("not a framework: %s" % framework_path)
                    continue
            # sign all the dylibs
            dylib_paths = glob.glob(join(frameworks_path, '*.dylib'))
            for dylib_path in dylib_paths:
                dylib = signable.Dylib(dylib_path)
                dylib.sign(self, signer)
        # then create the seal
        # TODO maybe the app should know what its seal path should be...
        self.seal_path = code_resources.make_seal(self.get_executable_path(),
                                                  self.path)
        # then sign the app
        executable = self.executable_class(self.get_executable_path())
        executable.sign(self, signer)

    def resign(self, signer):
        """ signs bundle, modifies in place """
        self.sign(signer)
        log.debug("Resigned bundle at <%s>", self.path)


class Framework(Bundle):
    """ A bundle that comprises reusable code. Similar to an app in that it has
        its own resources and metadata. Not like an app because the main executable
        doesn't have Entitlements, or an Application hash, and it doesn't have its
        own provisioning profile. """

    # the executable in this bundle will be a Framework
    executable_class = signable.Framework

    def __init__(self, path):
        super(Framework, self).__init__(path)


class App(Bundle):
    """ The kind of bundle that is visible as an app to the user.
        Contains the provisioning profile, entitlements, etc.  """

    # the executable in this bundle will be an Executable (i.e. the main
    # executaable of an app)
    executable_class = signable.Executable

    def __init__(self, path):
        super(App, self).__init__(path)
        self.entitlements_path = join(self.path,
                                      'Entitlements.plist')
        self.provision_path = join(self.path,
                                   'embedded.mobileprovision')

    def provision(self, provision_path):
        shutil.copyfile(provision_path, self.provision_path)

    def create_entitlements(self, team_id):
        entitlements = {
            "keychain-access-groups": [team_id + '.*'],
            "com.apple.developer.team-identifier": team_id,
            "application-identifier": team_id + '.*',
            "get-task-allow": True
        }
        biplist.writePlist(entitlements, self.entitlements_path, binary=False)
        # log.debug("wrote Entitlements to {0}".format(self.entitlements_path))

    def resign(self, signer, provisioning_profile):
        """ signs app in place """
        self.provision(provisioning_profile)
        self.create_entitlements(signer.team_id)
        super(App, self).resign(signer)
