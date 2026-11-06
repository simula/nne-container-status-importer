# nne-container-status-importer

Hourly batch importing the container heartbeat files of the NNE nodes into the report table `container_rawdata`, built
on the [nnedbtools](https://github.com/simula/nnedbtools) library. The nodes deliver the files with rsync into the inbox
`/nne/data/<node>/heartbeats/` on oculus; the batch imports every file present and deletes the imported ones. It is
owned and run as a service by the user `reversi`.

## Documentation

- [Installation](documentation/INSTALLATION.md): uv, Python 3.14.4, virtual environment with the project and nnedbtools
- [Usage](documentation/USAGE.md): command line, inbox and file format, processing and deletion, target table, database
  settings
- [Development](documentation/DEVELOPMENT.md): local test database, tests, local full import
