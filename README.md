# gbreakpad
google-breakpad for arm-linux

Compile:
> ./configure --prefix=/usr/local/gbreakpad/hisiv200 --build=i686-linux --host=arm-linux CC=arm-hisiv200-linux-gcc CXX=arm-linux-g++ CXXFLAGS=-DUNWIND_CONTEXT

> vi Makefile and comment the line of "am__append_2 = xxxxxxxxxxxx"

> make && make install

