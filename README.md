
# Procedure 

1) uv run downloaderV2.py
2) uv run image_generatorV2.py
3) Simulazioni Docker

##  3) Simulazione in locale con container Docker (prima il docker deamon deve essere aperto)

cd cloud-image-pipeline/resize


Per local testing basta anche solo: 

docker build -t resize_img .

Per immagine compatibile con aws:

docker build --platform linux/amd64 --provenance=false -t resize_img .

--platform fa si che venga costruito un container linux per il processore amd64 (compatibile con aws)
-- provencancew genera nua sola immagine e senza indici come invece sono le nuove immagini di Docker

docker run -p 9000:8080 \               # map host port 9000 → container port 8080
  -e LOCAL_TEST=true \                  # set environment variable for local mode
  -v /Users/…/all_test_images:/images:ro \  # mount test images (read-only)
  -v /Users/…/local_output:/output \     # mount output folder (read-write)
  -e LOCAL_OUTPUT_DIR=/output \         # tell script where to write inside container
  image-process:latest                   # the Docker image to run

### Esempio: 

docker run -d -p 9002:8080 \
  -e LOCAL_TEST=true \
  -v /Users/carloalbertoalfieri/Desktop/cloud/cloud-image-pipeline/Datav2/all_test_images:/images:ro \
  -v /Users/carloalbertoalfieri/Desktop/cloud/local_output:/output \
  -e LOCAL_OUTPUT_DIR=/output \
  resize_img:latest

Genera richiesta al tuo container: 

  curl -X POST "http://localhost:9002/2015-03-31/functions/function/invocations" \
  -d @/Users/carloalbertoalfieri/Desktop/cloud/event.json