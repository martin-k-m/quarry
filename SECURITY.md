# Security Policy

## Supported versions

Fixes land on the latest released version; there are no long-lived maintenance
branches.

## Reporting a vulnerability

Please report suspected vulnerabilities privately rather than in a public
issue. Use GitHub's [private vulnerability reporting](https://github.com/martin-k-m/quarry/security/advisories/new)
for this repository, or email martinkmuskov@gmail.com.

Include the query and the CSV that reproduce the problem. You can expect an
acknowledgement within a few days.

## Scope

`quarry` reads local CSV files named in a query and writes results to standard
output. It makes no network requests. It does not use Python's `eval` or any
dynamic execution: a query is tokenised, parsed into an abstract syntax tree,
and interpreted by the executor, so a query cannot escape into arbitrary Python.

A table name written without a path resolves to `name.csv` in the working
directory; a path with a dot or slash must be quoted. Point quarry only at
directories whose contents you trust, the same as any tool that opens files you
name on the command line.

The most likely class of issue is a crafted query or CSV that causes excessive
memory or an unhandled exception rather than a controlled `QueryError`. Those
are in scope and welcome.
