#!/bin/bash

set -x

# cleanup:
# sudo docker image ls | tail -n +2 | grep '<none>' | awk '{print $3;}' | xargs -n 1 sudo docker image rm

# jake 2020-01-14: pretty sure i'm doing this wrong, but note to self:
# remember to update README.md and ingress/rc.local
MNATV=0.0.5

for NAME in mnat-server mnat-ingress ; do
  IMG=$(sudo docker image ls $NAME | grep latest | awk '{print $3;}')
  sudo docker tag $IMG grumpyoldtroll/$NAME:latest
  sudo docker tag $IMG grumpyoldtroll/$NAME:$MNATV
  sudo docker push grumpyoldtroll/$NAME:$MNATV
  sudo docker push grumpyoldtroll/$NAME:latest
done
