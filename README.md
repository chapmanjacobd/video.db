# lb: opinionated media library

A wise philosopher once told me, "[The future is [...] auto-tainment](https://www.youtube.com/watch?v=F9sZFrsjPp0)".

Requires `ffmpeg`

## Install

    pip install xklb

## Quick Start -- filesystem

### 1. Extract Metadata

For the initial scan it takes about six hours to scan sixty terabytes. If you want to update the database run the same command again--any new files will be scanned and it is much, much quicker.

    lb extract tv.db ./video/folder/
    OR
    lb extract ./tv/  # when not specified, db will be created as `video.db`
    OR
    lb extract --audio ./music/  # db will be created as `audio.db`

### 2. Watch / Listen from local files

    wt tv.db                       # the default post-action is to do nothing after playing
    wt tv.db --post-action delete  # delete file after playing
    lt finalists.db --post-action=ask # ask to delete after playing

## Quick Start -- virtual

### 1. Download Metadata

Download playlist and channel metadata. Break free of the YouTube algo~

    lb tubeadd educational.db https://www.youtube.com/c/BranchEducation/videos

You can add more than one at a time.

    lb tubeadd maker.db https://www.youtube.com/c/CuriousMarc/videos https://www.youtube.com/c/element14presents/videos/ https://www.youtube.com/c/DIYPerks/videos

And you can always add more later--even from different websites.

    lb tubeadd maker.db https://vimeo.com/terburg

To prevent mistakes the default configuration is to download metadata for only the newest 20,000 videos per playlist/channel.

    lb tubeadd maker.db --yt-dlp-config playlistend=1000

Be aware that there are some YouTube Channels which have many items--for example the TEDx channel has about 180,000 videos. Some channels even have upwards of two million videos. More than you could likely watch in one sitting. On a high-speed connection (>500 Mbps), it can take up to five hours just to download the metadata for 180,000 videos. My advice: start with the 20,000.

#### 1a. Get new videos for saved playlists

Tubeupdate will go through all added playlists and fetch metadata of any new videos not previously seen.

    lb tubeupdate

You can also include your own yt-dlp download archive to skip downloaded videos and speed up playlist scanning.

    lb tubeupdate --yt-dlp-config download_archive=rel/loc/archive.txt

### 2. Watch / Listen from websites

    lb tubewatch maker.db

If you like this I also have a [web version](https://unli.xyz/eject/)--but this Python version has more features and it can handle a lot more data.

## Things to know

If you want to specify more than one directory you will need to mention the db file explicitly.

    lb extract --filesystem one/
    lb extract --filesystem fs.db one/ two/

Organize via separate databases.

    lb extract --audio both.db ./audiobooks/ ./podcasts/
    lb extract --audio audiobooks.db ./audiobooks/
    lb extract --audio podcasts.db ./podcasts/ ./another/more/secret/podcasts_folder/

## Usage

### Repeat

    lt -u random         # listen to ONE random song
    lt --limit 5         # listen to FIVE songs
    lt -l inf            # listen to songs indefinitely
    lt -s infinite       # listen to songs from the band infinite

If that is confusing (or if you are trying to load 4 billion files) you could always use your shell:

    function repeat
        while $argv
            and :
        end
    end

    repeat lt -s finite  # listen to finite songs infinitely

### Watch longest videos

    wt tv.db --sort duration desc

### Watch specific video series in order

    wt tv.db --search 'title of series' --play-in-order

There are multiple strictness levels of --play-in-order. If things aren't playing in order try adding more `O`s

    wt tv.db --search 'title of series' -O    # default
    wt tv.db --search 'title of series' -OO   # slower, more complex algorithm
    wt tv.db --search 'title of series' -OOO  # most strict

### Listen to OSTs on chromecast groups

    lt -cast -cast-to 'Office pair' -s '  ost'

### Exercise and watch TV that doesn't have subtitles

    wt -u priority -w sub=0

### Check if you've downloaded something before

    wt -u duration --print -s 'video title'

### View how much time you have listened to music

    lb lt -p a -w 'play_count>0'

### See how much video you have

    lb wt video.db -p a
    ╒═══════════╤═════════╤═════════╤═════════╕
    │ path      │   hours │ size    │   count │
    ╞═══════════╪═════════╪═════════╪═════════╡
    │ Aggregate │  145769 │ 37.6 TB │  439939 │
    ╘═══════════╧═════════╧═════════╧═════════╛
    Total duration: 16 years, 7 months, 19 days, 17 hours and 25 minutes

### Search the filesystem

You can also use `lb` for any files:

    $ lb extract -fs ~/d/41_8bit/

    $ lb fs fs.db -p a -s mario luigi
    ╒═══════════╤══════════════╤══════════╤═════════╕
    │ path      │   sparseness │ size     │   count │
    ╞═══════════╪══════════════╪══════════╪═════════╡
    │ Aggregate │            1 │ 215.0 MB │       7 │
    ╘═══════════╧══════════════╧══════════╧═════════╛

    $ lb fs -p -s mario -s luigi -s jpg -w is_dir=0 -u 'size desc'
    ╒═══════════════════════════════════════╤══════════════╤═════════╕
    │ path                                  │   sparseness │ size    │
    ╞═══════════════════════════════════════╪══════════════╪═════════╡
    │ /mnt/d/41_8bit/roms/gba/media/images/ │      1.05632 │ 58.2 kB │
    │ Mario & Luigi - Superstar Saga (USA,  │              │         │
    │ Australia).jpg                        │              │         │
    ├───────────────────────────────────────┼──────────────┼─────────┤
    │ /mnt/d/41_8bit/roms/gba/media/box3d/M │      1.01583 │ 44.4 kB │
    │ ario & Luigi - Superstar Saga (USA,   │              │         │
    │ Australia).jpg                        │              │         │
    ╘═══════════════════════════════════════╧══════════════╧═════════╛

### TODO

- all: Documentation
- all: is_deleted column
- all: split_by_silence without modifying files
- all: Tests
- tube: prevent adding duplicates
- tube: sqlite-utils
- tube: Download subtitle to embed in db tags for search
- tube: Playlists subcommand: view virtual aggregated pattens
- playlists: join indexes, delete, update
