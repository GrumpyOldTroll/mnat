#!/bin/bash

set +x
ingress/build.sh
server/build.sh
# NB: driad-ingest now comes from multicast-ingest-platform (as of 0.0.4)
# driad-ingest/build.sh
