pipeline {
    agent any

    environment {
        APP_NAME         = "inspecao"
        // Imagens no GitHub Container Registry (ghcr.io) — privadas, sob a org.
        // Nomes em minúsculo (exigência do ghcr).
        DOCKER_IMAGE_API = "ghcr.io/tecnogera-development/inspecao-api"
        DOCKER_IMAGE_WEB = "ghcr.io/tecnogera-development/inspecao-web"
        // Só o web publica porta no host (8094, reservada na VPS). API/worker/redis
        // ficam na rede do compose. O Cloudflare Tunnel alcança inspecao-web:8094 (rede infra).
        WEB_PORT         = "8094"
        // === AJUSTE para o ambiente real da VPS ===
        REMOTE_HOST      = "10.246.200.14"        // AJUSTE: host da VPS
        REMOTE_USER      = "tecnogera"            // AJUSTE: usuário SSH
        REMOTE_PORT      = "22"
        REMOTE_DIR       = "/opt/apps/inspecao-ocr"
        // Credencial do Jenkins com write:packages/read:packages na org Tecnogera-Development.
        GHCR_CRED        = "token_tecnogera_github"   // AJUSTE: criar no Jenkins
        SSH_CRED         = "ssh-tecnogera-rsa"        // AJUSTE se o id for outro
        COMPOSE_FILE     = "docker-compose.prod.yml"
        TAG              = "${BUILD_NUMBER}"
    }

    options {
        timestamps()
        disableConcurrentBuilds()
    }

    stages {

        stage('Checkout') {
            steps {
                checkout scm
            }
        }

        stage('Build Docker Images') {
            steps {
                sh '''
                    set -e
                    # API: alvo `runtime` (o default do Dockerfile é `dev`, com pytest/ruff).
                    # Contexto = tecnogera-ia-visual-api/ (Dockerfile copia app/, alembic/ etc.).
                    docker build --network=host --target runtime \
                        -f tecnogera-ia-visual-api/Dockerfile \
                        -t ${DOCKER_IMAGE_API}:${TAG} \
                        tecnogera-ia-visual-api
                    # Web (portal React + nginx). Contexto = tecnogera-portal/.
                    docker build --network=host \
                        -f tecnogera-portal/Dockerfile \
                        -t ${DOCKER_IMAGE_WEB}:${TAG} \
                        tecnogera-portal
                    docker tag ${DOCKER_IMAGE_API}:${TAG} ${DOCKER_IMAGE_API}:latest
                    docker tag ${DOCKER_IMAGE_WEB}:${TAG} ${DOCKER_IMAGE_WEB}:latest
                '''
            }
        }

        stage('Push to GHCR') {
            steps {
                withCredentials([usernamePassword(
                    credentialsId: env.GHCR_CRED,
                    usernameVariable: 'GH_USER',
                    passwordVariable: 'GH_TOKEN'
                )]) {
                    sh '''
                        set -e
                        echo "${GH_TOKEN}" | docker login ghcr.io -u "${GH_USER}" --password-stdin
                        docker push ${DOCKER_IMAGE_API}:${TAG}
                        docker push ${DOCKER_IMAGE_API}:latest
                        docker push ${DOCKER_IMAGE_WEB}:${TAG}
                        docker push ${DOCKER_IMAGE_WEB}:latest
                        docker logout ghcr.io
                    '''
                }
            }
        }

        stage('Run DB Migrations') {
            steps {
                withCredentials([usernamePassword(
                    credentialsId: env.GHCR_CRED,
                    usernameVariable: 'GH_USER',
                    passwordVariable: 'GH_TOKEN'
                )]) {
                    sshagent(credentials: [env.SSH_CRED]) {
                        sh '''
                            set -e

                            # Garante o diretório no destino e envia o compose de produção.
                            ssh -p ${REMOTE_PORT} -o StrictHostKeyChecking=no ${REMOTE_USER}@${REMOTE_HOST} \
                                "mkdir -p ${REMOTE_DIR}"
                            scp -P ${REMOTE_PORT} -o StrictHostKeyChecking=no \
                                ${COMPOSE_FILE} ${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR}/${COMPOSE_FILE}

                            # Autentica a VPS no GHCR (imagens privadas). O token vai pelo STDIN do ssh.
                            echo "${GH_TOKEN}" | ssh -p ${REMOTE_PORT} -o StrictHostKeyChecking=no ${REMOTE_USER}@${REMOTE_HOST} \
                                "docker login ghcr.io -u '${GH_USER}' --password-stdin"

                            ssh -p ${REMOTE_PORT} -o StrictHostKeyChecking=no ${REMOTE_USER}@${REMOTE_HOST} "
                                set -e
                                cd ${REMOTE_DIR}

                                if [ ! -f .env.prod ]; then
                                    echo 'ERRO: ${REMOTE_DIR}/.env.prod nao encontrado.'
                                    echo 'Copie o arquivo de variaveis para a VPS antes do primeiro deploy:'
                                    echo '  scp .env.prod ${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR}/.env.prod'
                                    exit 1
                                fi

                                # Imagens-alvo deste build (consumidas pelo compose via interpolacao).
                                printf 'API_IMAGE=%s\\nWEB_IMAGE=%s\\n' \
                                    '${DOCKER_IMAGE_API}:${TAG}' '${DOCKER_IMAGE_WEB}:${TAG}' > .deploy-image.env

                                echo '[migrate] Puxando imagens ${TAG}...'
                                docker compose -f ${COMPOSE_FILE} --env-file .env.prod --env-file .deploy-image.env pull migrate api worker web redis

                                echo '[migrate] Aplicando migracoes (alembic upgrade head — nao-destrutivo)...'
                                docker compose -f ${COMPOSE_FILE} --env-file .env.prod --env-file .deploy-image.env run --rm migrate

                                echo '[migrate] Migracoes aplicadas com sucesso.'
                            "
                        '''
                    }
                }
            }
        }

        stage('Deploy to VPS') {
            steps {
                sshagent(credentials: [env.SSH_CRED]) {
                    sh '''
                        set -e

                        ssh -p ${REMOTE_PORT} -o StrictHostKeyChecking=no ${REMOTE_USER}@${REMOTE_HOST} "
                            set -e
                            cd ${REMOTE_DIR}

                            # Salva as imagens atuais para rollback (antes de trocar).
                            PREV_API=\$(docker inspect inspecao-api --format='{{.Config.Image}}' 2>/dev/null || echo '')
                            PREV_WEB=\$(docker inspect inspecao-web --format='{{.Config.Image}}' 2>/dev/null || echo '')
                            printf 'API_IMAGE=%s\\nWEB_IMAGE=%s\\n' \"\${PREV_API}\" \"\${PREV_WEB}\" > .rollback-image.env

                            # Sobe redis + api + worker + web (migrate já rodou; Postgres é externo).
                            docker compose -f ${COMPOSE_FILE} --env-file .env.prod --env-file .deploy-image.env up -d redis api worker web

                            docker image prune -f
                            echo 'Deploy concluido: ${DOCKER_IMAGE_API}:${TAG} + ${DOCKER_IMAGE_WEB}:${TAG}'
                        "
                    '''
                }
            }
        }

        stage('Smoke Test') {
            steps {
                script {
                    sleep 25
                    sshagent(credentials: [env.SSH_CRED]) {
                        sh '''
                            set -e
                            ssh -p ${REMOTE_PORT} -o StrictHostKeyChecking=no ${REMOTE_USER}@${REMOTE_HOST} "
                                echo 'Verificando o web (SPA) em 127.0.0.1:${WEB_PORT}...'
                                curl -fsS -o /dev/null -w 'web HTTP %{http_code}\\n' http://127.0.0.1:${WEB_PORT}/

                                echo 'Aguardando a API ficar healthy...'
                                for i in \$(seq 1 12); do
                                    ST=\$(docker inspect inspecao-api --format='{{.State.Health.Status}}' 2>/dev/null || echo 'unknown')
                                    if [ \"\${ST}\" = 'healthy' ]; then break; fi
                                    sleep 5
                                done
                                echo \"api health: \${ST}\"
                                [ \"\${ST}\" = 'healthy' ] || { echo 'API nao ficou healthy'; exit 1; }

                                echo 'Containers em execucao:'
                                docker ps --filter name=inspecao --format 'table {{.Names}}\t{{.Status}}'
                            "
                        '''
                    }
                }
            }
        }
    }

    post {
        success {
            echo "Deploy ${APP_NAME} ${TAG} concluido com sucesso."
        }

        failure {
            echo "Falha detectada. Iniciando rollback automatico..."
            sshagent(credentials: [env.SSH_CRED]) {
                sh '''
                    ssh -p ${REMOTE_PORT} -o StrictHostKeyChecking=no ${REMOTE_USER}@${REMOTE_HOST} "
                        cd ${REMOTE_DIR}

                        if [ ! -f .rollback-image.env ]; then
                            echo 'Sem imagem anterior registrada. Rollback ignorado.'
                            exit 0
                        fi

                        PREV_API=\$(grep '^API_IMAGE=' .rollback-image.env | cut -d= -f2-)
                        PREV_WEB=\$(grep '^WEB_IMAGE=' .rollback-image.env | cut -d= -f2-)

                        if [ -z \"\${PREV_API}\" ] || [ -z \"\${PREV_WEB}\" ]; then
                            echo 'Nenhuma versao anterior completa encontrada. Rollback ignorado (1o deploy?).'
                            exit 0
                        fi

                        echo \"Revertendo para: api=\${PREV_API} web=\${PREV_WEB}\"
                        docker compose -f ${COMPOSE_FILE} --env-file .env.prod --env-file .rollback-image.env up -d redis api worker web
                        echo 'Rollback concluido.'
                    " || echo "Rollback falhou. Intervencao manual necessaria."
                '''
            }
        }
    }
}
