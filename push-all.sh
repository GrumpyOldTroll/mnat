#!/bin/bash


# cleanup:
# sudo docker image ls | tail -n +2 | grep '<none>' | awk '{print $3;}' | xargs -n 1 sudo docker image rm

VERSION=0.0.1

for NAME in mnat-server mnat-egress mnat-ingress driad-ingest; do
  IMG=$(sudo docker image ls $NAME | grep latest | awk '{print $3;}')
  sudo docker tag $IMG grumpyoldtroll/$NAME:$VERSION
  sudo docker push grumpyoldtroll/$NAME:$VERSION
  sudo docker push grumpyoldtroll/$NAME:latest
done
