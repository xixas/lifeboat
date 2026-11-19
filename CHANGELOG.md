# Changelog

## [0.5.0](https://github.com/xixas/lifeboat/compare/v0.4.0...v0.5.0) (2026-11-19)


### Features

* **app:** add Find more to back up on the Backups page ([f8ba66c](https://github.com/xixas/lifeboat/commit/f8ba66ca6211190af4360eb98d07542c7d1e0cd2))
* **app:** alert on Overview for dropped sources and new items found ([c9fad48](https://github.com/xixas/lifeboat/commit/c9fad4832d8a61466d02f817cffdb68ce1be833d))
* **app:** group the wizard's folders by catalogue group with ignore and restore ([d0bff4e](https://github.com/xixas/lifeboat/commit/d0bff4e4b343b8f12bb6c45388bbfbeb5ab3d9b9))
* **catalogue:** add a gtk-free catalogue and scan, and make suggest_folders use it ([362bd52](https://github.com/xixas/lifeboat/commit/362bd52ce87315030a5f82ca85dbd80d394ed98b))
* **cli:** add scan, ignore, unignore and add-job --links ([7c8613c](https://github.com/xixas/lifeboat/commit/7c8613c7e729c705dcab5a42c425dce55ac2b928))
* **config:** add a scan ignore list and mark ignored items in scan ([9f6abc4](https://github.com/xixas/lifeboat/commit/9f6abc4f549cd808c415b3d55bf4e11c39835c6f))
* **engine:** add links job key to store symlinks instead of following them ([5656ef6](https://github.com/xixas/lifeboat/commit/5656ef689dd3be345c4a3ccf3a53e8069cf9ac19))
* **engine:** alert when a folder or file stops being backed up ([fa19b41](https://github.com/xixas/lifeboat/commit/fa19b41f8c68e6b684c2d9cffd2e926f339f56b4))
* **found:** add gtk-free helpers for grouping, adding and ignoring catalogue items ([2a225ef](https://github.com/xixas/lifeboat/commit/2a225ef0e8714f07a67b9b23c1358c03ab0aa23b))
* **setup:** build jobs from catalogue items and group single files into home-files ([7366bf6](https://github.com/xixas/lifeboat/commit/7366bf69e7d410771977ed1655b737c0755205f3))
* **skills:** say whether your own skills are backed up, with an Add to Backup fix ([7723cc1](https://github.com/xixas/lifeboat/commit/7723cc1156bf2bdf70a84af4ee0732d775222e3b))


### Bug Fixes

* **app:** skip sizes for single files and flag the Skills page when your skills are not backed up ([19c408b](https://github.com/xixas/lifeboat/commit/19c408b6915a9245af45cfc5466256efe790cefe))
* **catalogue:** anchor the agent excludes so a skill named debug is still backed up ([78e8b09](https://github.com/xixas/lifeboat/commit/78e8b091b0de2164454208561a646d016e570b2d))
* **catalogue:** encrypt VS Code settings and exclude extension state, history and agent indexes ([8b3ffb6](https://github.com/xixas/lifeboat/commit/8b3ffb60fcbcae646cafdb1c8e87ebba8ed20256))
* **catalogue:** label linked folders by name and show the link count under the path ([326dd37](https://github.com/xixas/lifeboat/commit/326dd378cbed990e2e1962605f5be86b7996e0f4))
* **catalogue:** label the .claude folder Claude Code ([8f3e2ef](https://github.com/xixas/lifeboat/commit/8f3e2ef6ab31edc3fa1a7a2a91b7d5e163c0c970))
* **engine:** compare coverage sources after expanding ~ and normalising the path ([f7fc0f2](https://github.com/xixas/lifeboat/commit/f7fc0f24ea3b6a7b6cc1b6c0a8dba9cccfb889b6))
* **engine:** do not call the laptop fully covered while a dropped source is unacknowledged ([8bcc4df](https://github.com/xixas/lifeboat/commit/8bcc4dfbbcb223d82a10df0419d52267f2ea47cd))
* **found:** tell an upgrader about uncovered items; the wizard marks what it showed as seen ([e5bd670](https://github.com/xixas/lifeboat/commit/e5bd670ca0f0aff96a12e105022effaa5d46a175))
* **manifest:** carry include, exclude, links and filter_file through the manifest and refuse a bare ~ job ([8ed74a0](https://github.com/xixas/lifeboat/commit/8ed74a0ad7e526a71469a0a8050b6b8110c0debe))
* **setup:** give folders with the same name unique job names and destinations ([f916107](https://github.com/xixas/lifeboat/commit/f916107b685e64af01fd7bb35e3ee244bb81b112))
* **setup:** refuse a whole-home job only when restoring, so hand-written configs keep loading ([2dc43b3](https://github.com/xixas/lifeboat/commit/2dc43b3a98965a2fbb66e856d0144f9518d83b6b))

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
