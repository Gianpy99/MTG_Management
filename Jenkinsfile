// =============================================================================
// Jenkinsfile - Pipeline CI/CD per Middle-earth MTG Management
// -----------------------------------------------------------------------------
// Build dell'immagine Docker (FastAPI + SQLite) e deploy come container sul Pi.
// Il container serve UI + API su :8094 (dietro Caddy: /mtg/).
//
// Requisiti Jenkins:
//   - CLI docker disponibile (socket host montato)  [gia' configurato]
//   - Credenziale GitHub 'github-gianpy99' per il checkout del repo
// =============================================================================
pipeline {
    agent any

    options {
        disableConcurrentBuilds()
    }

    triggers {
        // Nessun webhook: Jenkins controlla GitHub ogni pochi minuti.
        pollSCM('H/3 * * * *')
    }

    environment {
        IMAGE     = 'mtg-collection:latest'
        CONTAINER = 'mtg-collection'
        HOST_PORT = '8094'
        DATA_VOL  = 'mtg-collection-data'
    }

    stages {
        stage('Build image') {
            steps {
                sh 'docker build -f Dockerfile -t $IMAGE .'
            }
        }

        stage('Deploy container') {
            steps {
                sh '''
                    docker rm -f $CONTAINER 2>/dev/null || true
                    docker volume create $DATA_VOL >/dev/null
                    docker run -d \
                        --name $CONTAINER \
                        --restart unless-stopped \
                        -p $HOST_PORT:8094 \
                        -e MTG_DATA_DIR=/app/data \
                        -v $DATA_VOL:/app/data \
                        $IMAGE
                '''
            }
        }

        stage('Health check') {
            steps {
                sh '''
                    sleep 6
                    docker exec $CONTAINER python -c "import urllib.request,sys; r=urllib.request.urlopen('http://localhost:8094/health', timeout=8); sys.exit(0 if r.status==200 else 1)"
                    echo "MTG Management OK su http://192.168.1.129:8094/"
                '''
            }
        }
    }

    post {
        success {
            echo 'Deploy MTG Management completato. UI: http://192.168.1.129:8094/  (Caddy: /mtg/)'
        }
        failure {
            echo 'Deploy MTG Management FALLITO. Log del container: docker logs mtg-collection'
        }
    }
}
