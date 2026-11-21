pipeline {
    agent any
    environment {
        IMAGE = 'vincent772/kirimcepat'
        TAG = "${env.BUILD_NUMBER}"
    }
    stages {
        stage('Checkout') {
            steps {
                checkout scm
            }
        }
        stage('Test') {
            steps {
                bat '''
                    @echo off
                    set "PATH=C:\\Docker;%PATH%"
                    set "DOCKER_HOST=tcp://localhost:2375"
                    docker build --target test -t %IMAGE%:test .
                '''
            }
        }
        stage('Build Image') {
            steps {
                bat '''
                    @echo off
                    set "PATH=C:\\Docker;%PATH%"
                    set "DOCKER_HOST=tcp://localhost:2375"
                    docker build -t %IMAGE%:%TAG% -t %IMAGE%:latest .
                '''
            }
        }
        stage('Push Image') {
            when { branch 'main' }
            steps {
                withCredentials([usernamePassword(credentialsId: 'dockerhub-credentials',
                                                  usernameVariable: 'USER',
                                                  passwordVariable: 'PASS')]) {
                    bat '''
                        @echo off
                        set "PATH=C:\\Docker;%PATH%"
                        set "DOCKER_HOST=tcp://localhost:2375"
                        docker login -u %USER% -p %PASS%
                        docker push %IMAGE%:%TAG%
                        docker push %IMAGE%:latest
                    '''
                }
            }
        }
        stage('Deploy') {
            when { branch 'main' }
            steps {
                bat '''
                    @echo off
                    set "PATH=C:\\Docker;%PATH%"
                    set "DOCKER_HOST=tcp://localhost:2375"
                    if not exist .env copy .env.example .env
                    docker compose pull app
                    docker compose up -d
                '''
            }
        }
        stage('Smoke Test') {
            when { branch 'main' }
            steps {
                bat '''
                    @echo off
                    timeout /t 10 /nobreak > nul
                    curl -fsS http://localhost:8000/health
                '''
            }
        }
    }
    post {
        success {
            echo 'Pipeline sukses: aplikasi KirimCepat (K13) tervalidasi dan ter-deploy!'
        }
        failure {
            echo 'Pipeline gagal: cek stage yang merah pada Stage View!'
        }
    }
}