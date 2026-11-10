# radish

an **opinionated**, vibe-coded [beets](https://beets.io) alternative

## Differences

- focus on singleton libraries as I don't have (m)any albums these days anyways
- no autotagging capabilities, metadata has to be edited by hand
- ships with batteries included for [cmus](https://cmus.github.io), vim and tmux
- uses [fzf](https://github.com/junegunn/fzf) to select artists

## Install

Make sure to have cmus, tmux, fzf and vim installed.

Then run `make install`

## Config

In order to edit meta data, your cmus config has to add the following bindings:

```
bind -f common o shell cmus-open
bind -f common e shell cmus-edit
bind -f common E shell cmus-edit --all
bind -f common D shell cmus-delete
```

## Import

Radish comes with an import subcommand, call `radish import [OPTIONS] PATHS...` to import files or folders into your `~/Music` directory.

## Vim

Radish comes with a bunch of utilities to help you edit meta data.

```
nnoremap <leader>a :call ReplaceArtists()<CR>
nnoremap <leader>A :call ClearAlbums()<CR>
nnoremap <leader>t :call SanitizeTitles()<CR>
```
