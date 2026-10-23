# Every target runs the task of the same name in makefile.py, which works without make too (Windows):
#
#   make test ARGS="-k policy"               python makefile.py test -k policy
#   make demo SERVICE=dynamodb               python makefile.py demo dynamodb
#   make local                               try the package as pip would install it, without publishing it
#   make local ARGS="--live --profile dev"   ... and against your own AWS account (read-only)
#   make local-agent                         the same with the AI agent and every package it needs (Python 3.11+)
#   make notebook                            JupyterLab on that locally installed package
#
# make help lists every task. PYTHON is the checkout's .venv when there is one, else python3.

PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
ARGS ?=
SERVICE ?= s3

TASKS := setup test lint check build docs docs-serve local local-agent notebook clean

.DEFAULT_GOAL := help
.PHONY: help demo $(TASKS)

help:
	@$(PYTHON) makefile.py help

demo:
	$(PYTHON) makefile.py demo $(SERVICE) $(ARGS)

$(TASKS):
	$(PYTHON) makefile.py $@ $(ARGS)
