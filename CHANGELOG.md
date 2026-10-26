# Changelog

## [0.4.0](https://github.com/xixas/lifeboat/compare/v0.3.2...v0.4.0) (2026-10-26)


### Features

* a readable run log, the same in run.log, the terminal and the Log tab ([b209a7f](https://github.com/xixas/lifeboat/commit/b209a7f02a8b33f332e0e124afed603b019a6b21))
* backup progress checklist with a Progress | Log view, Copy / Save Log and Try Again ([b209a7f](https://github.com/xixas/lifeboat/commit/b209a7f02a8b33f332e0e124afed603b019a6b21))
* per-device manifest and passphrase probe for restoring on a new laptop ([58259ca](https://github.com/xixas/lifeboat/commit/58259ca114da6c6d0934d4048cdc8b417d89b182))
* recommend restoring when the account already has this laptop's backup ([b209a7f](https://github.com/xixas/lifeboat/commit/b209a7f02a8b33f332e0e124afed603b019a6b21))
* restore wizard: bring a backup back onto a new laptop ([#8](https://github.com/xixas/lifeboat/issues/8)) ([6206b56](https://github.com/xixas/lifeboat/commit/6206b560ea1b5edc927d4d65aff1c45f0134d72d))
* setup wizard: sign in with Google, pick folders, save the recovery kit ([#7](https://github.com/xixas/lifeboat/issues/7)) ([65f2df9](https://github.com/xixas/lifeboat/commit/65f2df97512d420946bdddac2544640c18ab1368))
* show a lifeboat-branded page after google sign-in ([b209a7f](https://github.com/xixas/lifeboat/commit/b209a7f02a8b33f332e0e124afed603b019a6b21))
* sign-in, remotes, discovery and restore logic for the setup wizards ([#6](https://github.com/xixas/lifeboat/issues/6)) ([317f66c](https://github.com/xixas/lifeboat/commit/317f66ca503400a5679d4b3d265caea278a65a05))
* the backup dialog ends on Done and says when the next backup is ([b209a7f](https://github.com/xixas/lifeboat/commit/b209a7f02a8b33f332e0e124afed603b019a6b21))


### Bug Fixes

* find the example config when installed as a system package ([32983d0](https://github.com/xixas/lifeboat/commit/32983d0ae05742813699a46373e0dc8ee6bdcc4d))
* refuse encrypted jobs when the passphrase does not match the existing backup ([b209a7f](https://github.com/xixas/lifeboat/commit/b209a7f02a8b33f332e0e124afed603b019a6b21))
* say what a dry run would copy instead of already up to date ([b209a7f](https://github.com/xixas/lifeboat/commit/b209a7f02a8b33f332e0e124afed603b019a6b21))
* skip an empty folder whose backup is empty too, instead of failing the run ([#9](https://github.com/xixas/lifeboat/issues/9)) ([bae805a](https://github.com/xixas/lifeboat/commit/bae805a6c29dabc84a8a355e19903649043b643c))
* wrap the wizard button bar so two long pills no longer overflow the dialog ([b209a7f](https://github.com/xixas/lifeboat/commit/b209a7f02a8b33f332e0e124afed603b019a6b21))

## [0.3.2](https://github.com/xixas/lifeboat/compare/v0.3.1...v0.3.2) (2026-10-23)


### Bug Fixes

* run, restore and prune dialogs work on libadwaita 1.5 (Ubuntu 24.04) ([5e97e2b](https://github.com/xixas/lifeboat/commit/5e97e2b6bd3ead80c7046e767df4212c2b0f4193))

## [0.3.1](https://github.com/xixas/lifeboat/compare/v0.3.0...v0.3.1) (2026-10-23)


### Bug Fixes

* count agent-native skills as external on the skills page ([f7288dd](https://github.com/xixas/lifeboat/commit/f7288dd68ee964dacfb030c4ad3aeca02062417f))

## [0.3.0](https://github.com/xixas/lifeboat/compare/v0.2.0...v0.3.0) (2026-10-23)


### Features

* agent-friendly cli with json output and job management commands ([a62dab0](https://github.com/xixas/lifeboat/commit/a62dab0de4df41babb68e90c238204aec4bbba06))


### Documentation

* agent guide, lifeboat skill and tips for choosing what to back up ([10df833](https://github.com/xixas/lifeboat/commit/10df8338a5ada5b397c5900f3c75634cbc058874))
* rewrite the guides to be shorter and task-first ([868859f](https://github.com/xixas/lifeboat/commit/868859f655a30c33148e3ec8503dd257385ebf7a))
