# ~/.bashrc for cvemap container user
case $- in
  *i*) ;;
  *) return ;;
esac

export EDITOR=vim
export VISUAL=vim
export PAGER=less
export LESS="-FRX"
export HISTCONTROL=ignoreboth:erasedups
export HISTSIZE=5000
export HISTFILESIZE=10000

shopt -s histappend checkwinsize

if [ -f /etc/bash_completion ] && ! shopt -oq posix; then
  . /etc/bash_completion
fi

if [ -x /usr/bin/dircolors ]; then
  eval "$(dircolors -b)"
  alias ls='ls --color=auto'
  alias grep='grep --color=auto'
  alias egrep='egrep --color=auto'
  alias fgrep='fgrep --color=auto'
fi

alias ll='ls -alFh'
alias la='ls -A'
alias l='ls -CF'
alias vi='vim'
alias df='df -h'
alias free='free -h'
alias cvemap-maps='curl -fsS http://127.0.0.1:8000/api/maps | jq .'
alias ts-health='curl -i -sS http://127.0.0.1:8080/health'

if [ -f "${HOME}/.bash_prompt" ]; then
  . "${HOME}/.bash_prompt"
fi
