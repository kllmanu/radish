" radish utilities
"

function! ReplaceArtists()
  let artist_lines = []

  for lnum in range(1, line('$'))
    let line_text = getline(lnum)
    if line_text =~ '^artist: '
      call add(artist_lines, lnum)
    endif
  endfor

  if empty(artist_lines)
    echo "No artist lines found"
    return
  endif

  let result = system('radish artists')
  let result = substitute(result, '\n\+$', '', '') " Trim trailing newlines
  if empty(result)
    return
  endif

  for lnum in artist_lines
    call setline(lnum, 'artist: ' . result)
  endfor

  redraw!
endfunction

function! ClearAlbums()
  for lnum in range(1, line('$'))
    if getline(lnum) =~ '^album: '
      call setline(lnum, "album: ''")
    endif
  endfor
  redraw!
endfunction

function! SanitizeTitles()
  for lnum in range(1, line('$'))
    let line_text = getline(lnum)
    if line_text =~ '^title: '
      let new_title = substitute(line_text, '^title: \zs[^-]\+\s*-\s*', '', '')
      let new_title = substitute(new_title, '^\(.*\)$', '\1', '')
      call setline(lnum, new_title)
    endif
  endfor
  redraw!
endfunction
