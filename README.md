# radish

an **opinionated**, vibe-coded [beets](https://beets.io) alternative

tldr; I've got annoyed with beets to tag my music, I usually just download songs with `yt-dlp` and they already come with some meta data anyways. I want to have less artists (even if there are feat. or compilations) rather than multiple. I just prefer to organize my music library my own way. And I usually do so while listening to them with `cmus` inside a `tmux` session. Also I'm lazy, so `fzf` comes in handy. Oh and let's not forget about `vim`. 

## Differences

- focus on singleton libraries as I don't have (m)any albums these days anyways
- no autotagging capabilities, metadata has to be edited manually
- ships with batteries included for [cmus](https://cmus.github.io), vim and tmux
- uses [fzf](https://github.com/junegunn/fzf) to select existing artists

## Install

Make sure to have cmus, tmux, fzf and vim installed.

Then run `make install`. This installs the Bash completion script to
`~/.local/share/bash-completion/completions/radish`; restart Bash or source that
file to enable completion.

## Config

In order to edit meta data, your cmus config has to add the following bindings:

```
bind -f common o shell cmus-open
bind -f common e shell cmus-edit
bind -f common E shell cmus-edit --all
bind -f common D shell cmus-delete
```

## Import

Radish comes with an import subcommand, call `radish import [OPTIONS] PATHS...` to import files or folders into your `~/Music/Singletons` directory.

## Vim

Radish comes with a bunch of utilities to help you edit meta data.

```
nnoremap <leader>a :call ReplaceArtists()<CR>
nnoremap <leader>A :call ClearAlbums()<CR>
nnoremap <leader>t :call SanitizeTitles()<CR>
```
