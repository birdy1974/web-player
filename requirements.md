web-player: provide a complete new docker image/environment running on a synology nas ds918+. call the project and docker "web-player".
the application consists of 2 parts: 

1- private management console
main functionality: in the management console the admin can set which local files or (youtube or any other) urls can be played by the public web player. with user management.
admin should enable/disable which files/url will be visible for the web player (Playlist) for each user
the management console should also be able to select the output; either the public web-player and/or enigma2 satellite box (through a bouquet).
base the creation of the bouquets on:
https://github.com/birdy1974/vu-movie
https://github.com/birdy1974/stalker-proxy-manager

2- public web player (password protected)
main functionality: USER INTERFACE TO PLAY THE FILES. user can select what file to play from the playlist. user can search/jump to specific time stamp in the file. web player should be protected by user password
do a deep through search to find a easy to operate web player or base it on:
https://github.com/birdy1974/vu-movie
https://github.com/birdy1974/stalker-proxy-manager

give a working mock-up of the web interface before the actual implementation of the code so I can test before compiling everything
in case you do not understand anything or something is not clear or there are multiple options, you need to ask follow up questions with options to select and the impact on the application.
please warn me if there is a mismatch between the proposed database structure and the GUI or functionality, identify the problems and give options to make it better
