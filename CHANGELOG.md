# Changelog

## [0.6.0](https://github.com/xixas/lifeboat/compare/v0.5.0...v0.6.0) (2026-11-19)


### Features

* **app:** Config store page with status, push, sign out and a setup dialog ([9ebc99c](https://github.com/xixas/lifeboat/commit/9ebc99cd00b2c35dc87383dfec47daaef6bd254d))
* detect, adopt or create the skillshare config store ([94103f4](https://github.com/xixas/lifeboat/commit/94103f4a09e224f0821dc209ea9c603791a9f69d))
* **engine:** warn on Overview while skills are not uploading, friendlier upload and finding texts ([beec044](https://github.com/xixas/lifeboat/commit/beec044699cc68af57c927f6bf34a596f7fbf06d))
* GitHub App device-flow sign-in, installation check and per-invocation push ([3ff7c96](https://github.com/xixas/lifeboat/commit/3ff7c9690fec2ad419b701d91d256e08dcdf7f7e))
* pinned, hash-verified skillshare install ([0360407](https://github.com/xixas/lifeboat/commit/0360407eebe756302a631cf9332d7ebde6698075))
* read config store git state directly ([f893069](https://github.com/xixas/lifeboat/commit/f893069ca4048a7a12505fb98779b5b07fdf7983))
* scan and push the config store after the daily backup ([f957171](https://github.com/xixas/lifeboat/commit/f9571712df7b7af300642e75155fefa0acb09c33))
* scan uncommitted changes before a store push ([a4759c4](https://github.com/xixas/lifeboat/commit/a4759c4953965864247074341f4c74b060f4cdfe))
* secret scan and chaining pre-push hook for the config store ([ae69da0](https://github.com/xixas/lifeboat/commit/ae69da0595969d2e44b21749d8d0057b4dd18f22))
* **secretscan:** make the pinned gitleaks the primary scanner, fail closed for Lifeboat's own push, and catch JSON keys and bearer headers ([08b7f42](https://github.com/xixas/lifeboat/commit/08b7f428e1ed9e65f63b12e80249427c5d63e0e0))
* **store:** helpers for the setup flow (github_repo, prepare, find_repo, connect_remote, start_ui) and set_store_text ([515830c](https://github.com/xixas/lifeboat/commit/515830c5ee12599a6bc37559e3f7e5b441a6cfdb))
* **store:** install a pinned, hash-verified gitleaks 8.30.1 next to skillshare ([afc8ee5](https://github.com/xixas/lifeboat/commit/afc8ee5b1c3da969e32f46fa374f215f5f344b5d))
* **store:** rework the Skills on GitHub page and setup dialog after the UX review ([f274d24](https://github.com/xixas/lifeboat/commit/f274d246a04c9727cdcc10b8ca167567ade52144))


### Bug Fixes

* **app:** disable Push now while signed out and use sentence case for the revoked-access title ([068455a](https://github.com/xixas/lifeboat/commit/068455a4f80edac03426554d39230174e9ee2288))
* **app:** name skillshare in install errors and drop the lone step number for an existing repo ([9c4a249](https://github.com/xixas/lifeboat/commit/9c4a2493c301c35dc861fffb7b256426a70c7234))
* **github:** give git the credential helper through GIT_CONFIG_COUNT, not -c ([2a5c069](https://github.com/xixas/lifeboat/commit/2a5c069211a3579dd96491ea8b31c7855cba3bbf))
* **github:** pass the push token in a private file, not the environment git hands to hooks ([646dcc5](https://github.com/xixas/lifeboat/commit/646dcc58e849595d487b058f957fb3f84e3de368))
* **github:** remove the token file on SIGTERM and SIGHUP during a push ([03503d8](https://github.com/xixas/lifeboat/commit/03503d8742af7f7979ebe6242481c8899624217a))
* **github:** treat a token with no expires_in as non-expiring and never refresh it ([2d2abb1](https://github.com/xixas/lifeboat/commit/2d2abb15eff45e5d36fbb2bc97fbd735612a5c08))
* **secretscan:** hook calls lifeboat on PATH and fails open, add an allowlist, tighten the generic secret rule ([9b1866e](https://github.com/xixas/lifeboat/commit/9b1866e3df24f9a273fc6ca6d0ccff21d3077e68))
* **secretscan:** install and chain the hook in core.hooksPath when it is set ([9d66298](https://github.com/xixas/lifeboat/commit/9d66298f3f9d53f591a7c7077d043e658d5a5969))
* **secretscan:** never write into a global core.hooksPath, use a repo-local hooks folder that chains to it ([3c4d810](https://github.com/xixas/lifeboat/commit/3c4d810cb2067e0a4b1937381a3f5792f4236d56))
* **secretscan:** run the hook's Lifeboat by absolute package path and fall back to PATH when the probe fails ([12b193f](https://github.com/xixas/lifeboat/commit/12b193f40d57a96b0eb61a09c3cbf0ad810a2725))
* **secretscan:** run the hook's python fallback in isolated mode so the pushed repo's lifeboat folder is never imported ([35e370a](https://github.com/xixas/lifeboat/commit/35e370abbe2f6ed0f2fd9f000661440735b8a7ec))
* **secretscan:** scan every commit in the pushed range and tell the user how to rewrite the unpushed one ([bc573c6](https://github.com/xixas/lifeboat/commit/bc573c64ceb3e48cd053be83c23f587862851d88))
* **secretscan:** scan from Lifeboat's own pushed refs instead of every remote-tracking ref ([cea689e](https://github.com/xixas/lifeboat/commit/cea689e76c1c6f15b961db0000eaa1dc15ccbc52))
* **secretscan:** scan non-ASCII file names and merge resolutions ([7ce57d3](https://github.com/xixas/lifeboat/commit/7ce57d3fb89d2551d6389901cff7ad0446990918))
* **secretscan:** scan the whole content of every unpushed, staged and edited file instead of parsing diff text ([8548671](https://github.com/xixas/lifeboat/commit/8548671f9004947bb4cfa78cabaddc08dfeae887))
* **secretscan:** the hook allows a push only on a clean scan, and skips only when lifeboat is absent or too old ([e52cbe7](https://github.com/xixas/lifeboat/commit/e52cbe71a0777762265f5250899cc4b740f7a377))
* **secretscan:** write the absolute path of lifeboat into the pre-push hook, with PATH as the fallback and no external commands before it ([00f66e8](https://github.com/xixas/lifeboat/commit/00f66e8211385bcbac6654e7a097c1b30fbd1bf6))
* **store:** a configured store always pushes with the Lifeboat GitHub token, never with system credentials ([3d823f5](https://github.com/xixas/lifeboat/commit/3d823f5c6bbbfc7fafa55149341a628b337e9078))
* **store:** a signed-out store never pushes with another login, and the page offers Sign in ([34e7f07](https://github.com/xixas/lifeboat/commit/34e7f072aa8cdae73c3b7a4146a086a820967e13))
* **store:** clear stale token files at startup and run the app's Push now as a lifeboat store-push subprocess ([2dd0f3d](https://github.com/xixas/lifeboat/commit/2dd0f3dbbbe8c97fbaefbd94d7c6de295aa536c8))
* **store:** push a new store with -u origin on the first run ([36f79cf](https://github.com/xixas/lifeboat/commit/36f79cfe353cbba2a1d94b6bb93f385e96be8046))
* **store:** push only to the confirmed https://github.com/&lt;owner&gt;/&lt;repo&gt;.git with a clean git environment, never to origin or any configured URL ([078a93d](https://github.com/xixas/lifeboat/commit/078a93d64d114815430f3b6ceb1bb0835418d0cf))
* **store:** refuse a skillshare source that is not a git repository instead of writing a hook into the cwd ([57c7c0b](https://github.com/xixas/lifeboat/commit/57c7c0b9f2fbeff2942df035487ccf77914da211))
* **store:** require the store source to be the git repo root so a subfolder is never committed, scanned or hooked as a whole repo ([3eb6fa1](https://github.com/xixas/lifeboat/commit/3eb6fa1a1ad86700a8ea6c84b61068bdd258d9c4))
* **store:** resume an interrupted setup instead of running skillshare init twice ([db699c0](https://github.com/xixas/lifeboat/commit/db699c023ac419b31b0904439ea866c6fdd70093))

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
