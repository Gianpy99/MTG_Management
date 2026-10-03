// =============================================================================
// Jenkinsfile - Pipeline CI/CD per Middle-earth MTG Management
// -----------------------------------------------------------------------------
// Build dell'immagine Docker (FastAPI + SQLite) e deploy come container sul Pi.
// Il container serve UI + API su :8094 (dietro Caddy: /mtg/).
//
// Forge Engine (forge/): immagine separata 'mtg-forge' (Forge CLI + Java 17 +
// API a coda su :8787). Versione di Forge fissata in forge/forge.version:
// per aggiornare Forge basta modificare quel file e fare push su main.
// Gli step Forge sono in forge/scripts/forge_pipeline.sh (unit test, build,
// partita Forge reale, deploy, partita post-deploy, rollback automatico).
// Un errore Forge fa fallire la build ma NON blocca il deploy dell'app
// (che mostrera' Forge "offline" o restera' sulla versione precedente).
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
        // Rete Docker condivisa: l'app raggiunge Forge come http://mtg-forge:8787
        MTG_NETWORK = 'mtg-net'
        FORGE_URL   = 'http://mtg-forge:8787'
    }

    stages {
        stage('Build image') {
            steps {
                sh 'docker build -f Dockerfile -t $IMAGE .'
            }
        }

        stage('Forge unit tests') {
            steps {
                catchError(buildResult: 'FAILURE', stageResult: 'FAILURE') {
                    sh 'bash forge/scripts/forge_pipeline.sh unit'
                }
            }
        }

        stage('Build Forge image') {
            when { expression { currentBuild.currentResult == 'SUCCESS' } }
            steps {
                catchError(buildResult: 'FAILURE', stageResult: 'FAILURE') {
                    sh 'bash forge/scripts/forge_pipeline.sh build'
                }
            }
        }

        stage('Forge integration test') {
            when { expression { currentBuild.currentResult == 'SUCCESS' } }
            steps {
                catchError(buildResult: 'FAILURE', stageResult: 'FAILURE') {
                    sh 'bash forge/scripts/forge_pipeline.sh integration'
                }
            }
        }

        stage('Deploy Forge + smoke test') {
            when { expression { currentBuild.currentResult == 'SUCCESS' } }
            steps {
                catchError(buildResult: 'FAILURE', stageResult: 'FAILURE') {
                    sh 'bash forge/scripts/forge_pipeline.sh deploy'
                }
            }
        }

        stage('Deploy container') {
            steps {
                sh '''
                    docker network inspect $MTG_NETWORK >/dev/null 2>&1 || docker network create $MTG_NETWORK
                    docker rm -f $CONTAINER 2>/dev/null || true
                    docker volume create $DATA_VOL >/dev/null
                    docker run -d \
                        --name $CONTAINER \
                        --restart unless-stopped \
                        --network $MTG_NETWORK \
                        -p $HOST_PORT:8094 \
                        -e MTG_DATA_DIR=/app/data \
                        -e FORGE_URL=$FORGE_URL \
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

        stage('Forge reachable from app') {
            steps {
                catchError(buildResult: 'UNSTABLE', stageResult: 'UNSTABLE') {
                    sh '''
                        docker exec $CONTAINER python -c "import json,urllib.request,sys; s=json.load(urllib.request.urlopen('http://localhost:8094/api/forge/status', timeout=10)); print('Forge visto dall app:', s.get('status'), s.get('forge_version'), s.get('java_version'), s.get('error', '')); sys.exit(0 if s.get('status')=='ready' else 1)"
                    '''
                }
            }
        }
    }

    post {
        success {
            echo 'Deploy completato. UI: http://192.168.1.129:8094/ (Caddy: /mtg/) - Forge: http://192.168.1.129:8787/api/forge/status'
        }
        unstable {
            echo 'App deployata ma il Forge Engine non risponde: docker logs mtg-forge'
        }
        failure {
            echo 'Deploy FALLITO. Log: docker logs mtg-collection / docker logs mtg-forge'
        }
    }
}
