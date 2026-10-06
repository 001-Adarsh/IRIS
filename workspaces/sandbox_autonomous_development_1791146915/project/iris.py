from core.agent import IRISAgent


def main():

    agent = IRISAgent()

    print("\n================================")
    print("        IRIS AI ASSISTANT")
    print("================================")
    print("Type 'exit', 'quit' or '/bye' to close IRIS.\n")


    while True:

        try:

            user_input = input("You: ").strip()

        except (KeyboardInterrupt, EOFError):

            print("\nIRIS closed.")
            break


        if not user_input:
            continue


        print("\nIRIS:")


        try:

            response = agent.run(user_input)


            if response == "__IRIS_EXIT__":

                print("Goodbye.")
                break


            print(response)


        except Exception as e:

            print(f"IRIS Error: {e}")


        print()


if __name__ == "__main__":
    main()
